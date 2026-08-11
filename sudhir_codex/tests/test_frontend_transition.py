import hashlib
import json
import os
import plistlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sudhir_codex_gateway._frontend_transition_state import BASELINE_CONFIG
from sudhir_codex_gateway._frontend_transition_state import TRANSITION_METADATA
from sudhir_codex_gateway._frontend_transition_state import TransitionPaths
from sudhir_codex_gateway._frontend_transition_state import prepare
from sudhir_codex_gateway._frontend_transition_state import restore_chrome
from sudhir_codex_gateway._frontend_transition_state import rewrite_transition_config
from sudhir_codex_gateway.frontend_transition import rollback


class FrontendTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        (self.repo / "dist").mkdir(parents=True)
        self.primary = self.root / "primary"
        self.primary.mkdir()
        self.transition = self.root / "transition"
        self.profile = self.root / "profile"
        self.app = self.root / "ChatGPT.app"
        self.chrome_manifest = self.root / "chrome" / "manifest.json"
        self.pi = self.root / "pi"
        self.pi.mkdir()
        self.launcher = Path.home() / ".local" / "bin" / "sudhir-codex"
        self.paths = TransitionPaths(
            repo_root=self.repo,
            primary_state=self.primary,
            state=self.transition,
            profile=self.profile,
            official_app=self.app,
            chrome_manifest=self.chrome_manifest,
            pi_agent_dir=self.pi,
        )
        self._seed_primary_state()
        self._seed_app()
        self.chrome_manifest.parent.mkdir(parents=True)
        self.chrome_manifest.write_text('{"path":"original-host"}\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _seed_primary_state(self) -> None:
        config = f'''notify = ["{self.primary}/computer-use/Codex Computer Use.app/client", "turn-ended"]

[mcp_servers.node_repl]
command = "/Applications/Sudhir-Codex.app/Contents/Resources/cua_node/bin/node_repl"

[mcp_servers.node_repl.env]
NODE_REPL_NODE_MODULE_DIRS = "/Applications/Sudhir-Codex.app/Contents/Resources/cua_node/lib/node_modules"
NODE_REPL_NODE_PATH = "/Applications/Sudhir-Codex.app/Contents/Resources/cua_node/bin/node"
NODE_REPL_TRUSTED_CODE_PATHS = "{self.primary}"
NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "old-browser-hash"

[shell_environment_policy.set]
NODE_REPL_TRUSTED_CODE_PATHS = "{self.primary}"
NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "old-browser-hash"
CODEX_HOME = "{self.primary}"
BROWSER_USE_AVAILABLE_BACKENDS = "chrome,iab"
BROWSER_USE_CODEX_APP_VERSION = "old-version"
SKY_CUA_SERVICE_PATH = "{self.primary}/control/Sudhir Computer Use.app"
SKY_CUA_SERVICE_NATIVE_PIPE_PATH = "{self.primary}/control/run/computer-use.sock"
CODEX_CLI_PATH = "{self.launcher}"
'''
        (self.primary / "config.toml").write_text(config, encoding="utf-8")
        os.chmod(self.primary / "config.toml", 0o600)
        (self.primary / "computer-use" / "Codex Computer Use.app").mkdir(parents=True)
        (self.primary / "gateway").mkdir()
        (self.primary / "gateway" / "gateway.pid").write_text("123\n")
        (self.primary / "ipc").mkdir()
        (self.repo / "dist" / "sudhir-codex-core").write_bytes(b"core")

    def _seed_app(self) -> None:
        plist = self.app / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True)
        with plist.open("wb") as handle:
            plistlib.dump(
                {
                    "CFBundleIdentifier": "com.openai.codex",
                    "CFBundleShortVersionString": "26.test",
                    "CFBundleVersion": "1234",
                },
                handle,
            )
        resources = self.app / "Contents" / "Resources"
        (resources / "cua_node" / "bin").mkdir(parents=True)
        (resources / "cua_node" / "bin" / "node_repl").write_bytes(b"node-repl")
        (resources / "cua_node" / "bin" / "node").write_bytes(b"node")
        (resources / "cua_node" / "lib" / "node_modules").mkdir(parents=True)
        browser = (
            resources
            / "plugins"
            / "openai-bundled"
            / "plugins"
            / "chrome"
            / "scripts"
            / "browser-client.mjs"
        )
        browser.parent.mkdir(parents=True)
        browser.write_bytes(b"browser-client")

    def test_rewrite_uses_official_resources_and_removes_custom_pipe(self) -> None:
        source = (self.primary / "config.toml").read_text(encoding="utf-8")
        browser_hash = hashlib.sha256(b"browser-client").hexdigest()

        rewritten = rewrite_transition_config(
            source,
            primary_state=self.primary,
            transition_state=self.transition,
            official_app=self.app,
            official_version="26.test",
            browser_client_hash=browser_hash,
        )

        self.assertIn(str(self.transition), rewritten)
        self.assertIn(str(self.app / "Contents" / "Resources" / "cua_node"), rewritten)
        self.assertEqual(rewritten.count(browser_hash), 2)
        self.assertNotIn("old-browser-hash", rewritten)
        self.assertEqual(
            rewritten.count(f'NODE_REPL_TRUSTED_CODE_PATHS = "{self.transition}:'),
            2,
        )
        self.assertIn("26.test", rewritten)
        self.assertIn(
            str(self.transition / "computer-use" / "Codex Computer Use.app"),
            rewritten,
        )
        self.assertNotIn("Sudhir Computer Use.app", rewritten)
        self.assertNotIn("SKY_CUA_SERVICE_NATIVE_PIPE_PATH", rewritten)

    def test_prepare_isolates_state_and_preserves_reversible_snapshots(self) -> None:
        primary_config = self.primary / "config.toml"
        primary_hash = hashlib.sha256(primary_config.read_bytes()).hexdigest()

        with (
            mock.patch(
                "sudhir_codex_gateway._frontend_transition_state._verify_official_resources",
                return_value=(
                    "com.openai.codex",
                    "26.test",
                    "1234",
                    hashlib.sha256(b"browser-client").hexdigest(),
                ),
            ),
            mock.patch(
                "sudhir_codex_gateway._frontend_transition_state._clone_state",
                side_effect=lambda source, destination: shutil.copytree(
                    source, destination
                ),
            ),
            mock.patch(
                "sudhir_codex_gateway._frontend_transition_state._git_head",
                return_value="commit-test",
            ),
            mock.patch.object(
                TransitionPaths,
                "installed_launcher",
                new_callable=mock.PropertyMock,
                return_value=self.repo / "installed-launcher",
            ),
        ):
            (self.repo / "installed-launcher").write_text("launcher")
            metadata = prepare(self.paths)

        self.assertEqual(
            hashlib.sha256(primary_config.read_bytes()).hexdigest(), primary_hash
        )
        self.assertTrue((self.transition / BASELINE_CONFIG).is_file())
        self.assertTrue((self.transition / TRANSITION_METADATA).is_file())
        self.assertTrue(self.profile.is_dir())
        self.assertEqual(metadata["primaryConfigSha256"], primary_hash)
        self.assertRegex(metadata["primaryConfigSemanticSha256"], r"^[0-9a-f]{64}$")
        wrapper = (self.transition / "bin" / "sudhir-codex-chatgpt").read_text()
        self.assertIn(f"SUDHIR_CODEX_STATE={json.dumps(str(self.transition))}", wrapper)
        self.assertIn(
            f"SUDHIR_CODEX_GATEWAY_STATE={json.dumps(str(self.primary))}", wrapper
        )
        self.assertEqual(
            (
                self.transition
                / "frontend-transition-backups"
                / "com.openai.codexextension.json"
            ).read_bytes(),
            self.chrome_manifest.read_bytes(),
        )

    def test_chrome_restore_and_rollback_archive_without_touching_primary(self) -> None:
        self.transition.mkdir()
        self.profile.mkdir()
        (self.transition / "frontend-transition-backups").mkdir()
        original_manifest = b'{"path":"original-host"}\n'
        backup = (
            self.transition
            / "frontend-transition-backups"
            / "com.openai.codexextension.json"
        )
        backup.write_bytes(original_manifest)
        primary_hash = hashlib.sha256(
            (self.primary / "config.toml").read_bytes()
        ).hexdigest()
        metadata = {
            "primaryState": str(self.primary),
            "primaryConfigSha256": primary_hash,
            "chromeManifestPresent": True,
        }
        (self.transition / TRANSITION_METADATA).write_text(json.dumps(metadata))
        self.chrome_manifest.write_bytes(b'{"path":"changed-host"}\n')

        restore_chrome(self.paths)
        self.assertEqual(self.chrome_manifest.read_bytes(), original_manifest)

        with mock.patch(
            "sudhir_codex_gateway.frontend_transition.transition_processes",
            return_value=[],
        ):
            destination = rollback(self.paths)

        self.assertFalse(self.transition.exists())
        self.assertFalse(self.profile.exists())
        self.assertTrue((destination / self.transition.name).is_dir())
        self.assertTrue((destination / self.profile.name).is_dir())
        self.assertEqual(
            hashlib.sha256((self.primary / "config.toml").read_bytes()).hexdigest(),
            primary_hash,
        )

    def test_rollback_allows_only_volatile_marketplace_timestamp_change(self) -> None:
        primary_config = self.primary / "config.toml"
        primary_config.write_text(
            primary_config.read_text()
            + "\n"
            + '[marketplaces."openai-bundled"]\n'
            + 'last_updated = "old"\n'
        )
        self.transition.mkdir()
        self.profile.mkdir()
        (self.transition / "frontend-transition-backups").mkdir()
        baseline = self.transition / BASELINE_CONFIG
        baseline.write_bytes(primary_config.read_bytes())
        primary_hash = hashlib.sha256(primary_config.read_bytes()).hexdigest()
        metadata = {
            "primaryState": str(self.primary),
            "primaryConfigSha256": primary_hash,
            "chromeManifestPresent": False,
        }
        (self.transition / TRANSITION_METADATA).write_text(json.dumps(metadata))
        primary_config.write_text(
            primary_config.read_text().replace(
                'last_updated = "old"',
                'last_updated = "new"',
            )
        )

        with mock.patch(
            "sudhir_codex_gateway.frontend_transition.transition_processes",
            return_value=[],
        ):
            destination = rollback(self.paths)

        self.assertTrue((destination / self.transition.name).is_dir())
        self.assertTrue(primary_config.is_file())

    def test_rollback_fails_before_moving_state_when_primary_changed(self) -> None:
        self.transition.mkdir()
        self.profile.mkdir()
        (self.transition / "frontend-transition-backups").mkdir()
        primary_hash = hashlib.sha256(
            (self.primary / "config.toml").read_bytes()
        ).hexdigest()
        metadata = {
            "primaryState": str(self.primary),
            "primaryConfigSha256": primary_hash,
            "chromeManifestPresent": False,
        }
        (self.transition / TRANSITION_METADATA).write_text(json.dumps(metadata))
        (self.primary / "config.toml").write_text("changed = true\n")

        with mock.patch(
            "sudhir_codex_gateway.frontend_transition.transition_processes",
            return_value=[],
        ):
            with self.assertRaisesRegex(RuntimeError, "Primary config"):
                rollback(self.paths)

        self.assertTrue(self.transition.is_dir())
        self.assertTrue(self.profile.is_dir())


if __name__ == "__main__":
    unittest.main()
