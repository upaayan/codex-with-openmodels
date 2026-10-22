import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from sudhir_codex_gateway._frontend_transition_control import _rewrite_instructions
from sudhir_codex_gateway._frontend_transition_control import (
    _rewrite_native_pipe_source,
)
from sudhir_codex_gateway._frontend_transition_control import _wrapper_text
from sudhir_codex_gateway._frontend_transition_control import _primary_wrapper_text
from sudhir_codex_gateway._frontend_transition_control import (
    apply_primary_control_config,
)
from sudhir_codex_gateway._frontend_transition_state import BASELINE_CONFIG
from sudhir_codex_gateway._frontend_transition_state import TRANSITION_METADATA
from sudhir_codex_gateway._frontend_transition_state import TransitionPaths
from sudhir_codex_gateway._frontend_transition_state import prepare
from sudhir_codex_gateway._frontend_transition_state import restore_chrome
from sudhir_codex_gateway._frontend_transition_state import rewrite_transition_config
from sudhir_codex_gateway.frontend_transition import ensure
from sudhir_codex_gateway.frontend_transition import launch
from sudhir_codex_gateway.frontend_transition import rollback
from sudhir_codex_gateway.frontend_transition import sync_control_runtime
import sudhir_codex_gateway._frontend_transition_control as frontend_control
import sudhir_codex_gateway.frontend_transition as frontend_transition


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
        self.legacy_cua_source = self.root / "legacy-cua"
        self.launcher = Path.home() / ".local" / "bin" / "sudhir-codex"
        self.paths = TransitionPaths(
            repo_root=self.repo,
            primary_state=self.primary,
            state=self.transition,
            profile=self.profile,
            official_app=self.app,
            chrome_manifest=self.chrome_manifest,
            pi_agent_dir=self.pi,
            legacy_cua_source=self.legacy_cua_source,
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
BROWSER_USE_CODEX_APP_VERSION = "old-version"
SKY_CUA_SERVICE_PATH = "{self.primary}/control/Sudhir Computer Use.app"
SKY_CUA_SERVICE_NATIVE_PIPE_PATH = "{self.primary}/control/run/computer-use.sock"
CODEX_CLI_PATH = "{self.launcher}"

[shell_environment_policy.set]
NODE_REPL_TRUSTED_CODE_PATHS = "{self.primary}"
NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "old-browser-hash"
CODEX_HOME = "{self.primary}"
BROWSER_USE_AVAILABLE_BACKENDS = "chrome,iab"
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
        executor = resources / "codex-cli/CodexCLI.app/Contents/MacOS/codex"
        executor.parent.mkdir(parents=True)
        executor.write_bytes(b"official-executor")
        executor.chmod(0o755)
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
            legacy_cua_source=self.legacy_cua_source,
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
            str(
                self.transition
                / "frontend-control-runtime"
                / "legacy-cua"
                / "bin"
                / "node_repl"
            ),
            rewritten,
        )
        self.assertIn(
            str(self.primary / "control" / "Sudhir Computer Use.app"),
            rewritten,
        )
        self.assertIn(
            str(self.primary / "control" / "run" / "computer-use.sock"),
            rewritten,
        )
        self.assertIn("SUDHIR_CUA_SERVICE_PATH", rewritten)
        self.assertIn("SUDHIR_CUA_SERVICE_NATIVE_PIPE_PATH", rewritten)
        self.assertNotIn("/Applications/Sudhir-Codex.app", rewritten)

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
            mock.patch(
                "sudhir_codex_gateway._frontend_transition_state.provision_control_runtime",
                return_value={
                    "controlNodeReplSha256": "node-repl-hash",
                    "controlPluginVersions": ["1.0.test"],
                },
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
        self.assertEqual(metadata["controlNodeReplSha256"], "node-repl-hash")
        self.assertEqual(metadata["legacyCuaSource"], str(self.legacy_cua_source))
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

    def test_launch_uses_isolated_control_runtime(self) -> None:
        self.transition.mkdir()
        self.profile.mkdir()
        (self.transition / "bin").mkdir()
        wrapper = self.transition / "bin" / "sudhir-codex-chatgpt"
        wrapper.write_text("#!/bin/sh\n")
        wrapper.chmod(0o755)
        (self.transition / "config.toml").write_text(
            (self.primary / "config.toml").read_text()
        )
        (self.transition / TRANSITION_METADATA).write_text(
            json.dumps({"browserClientSha256": "official-browser-hash"})
        )

        with (
            mock.patch("sudhir_codex_gateway.frontend_transition.sync_control_runtime"),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._wait_for_app_server",
                return_value=False,
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.subprocess.run"
            ) as run,
        ):
            launch(self.paths)

        command = run.call_args.args[0]
        control = self.paths.control_runtime
        self.assertIn(f"CODEX_NODE_REPL_PATH={control.node_repl}", command)
        self.assertIn(f"CODEX_BROWSER_USE_NODE_PATH={control.node}", command)
        self.assertIn("SKY_CUA_SERVICE_PATH=", command)
        self.assertIn("SKY_CUA_SERVICE_NATIVE_PIPE_PATH=", command)
        self.assertNotIn(f"SKY_CUA_SERVICE_PATH={control.helper}", command)
        self.assertNotIn(
            f"SKY_CUA_SERVICE_NATIVE_PIPE_PATH={control.socket}",
            command,
        )
        self.assertIn("SUDHIR_CUA=0", command)
        self.assertIn(
            "SUDHIR_BROWSER_CLIENT_SHA256S=official-browser-hash",
            command,
        )

    def test_launch_primary_provisions_stable_computer_use_before_launch(
        self,
    ) -> None:
        self.assertTrue(
            hasattr(frontend_transition, "launch_primary"),
            "primary-home launch mode is missing",
        )
        launch_primary = frontend_transition.launch_primary
        self.transition.mkdir()
        self.profile.mkdir()
        (self.transition / TRANSITION_METADATA).write_text(
            json.dumps({"browserClientSha256": "official-browser-hash"})
        )

        events: list[str] = []

        def sync_primary(_paths: TransitionPaths) -> dict[str, str]:
            events.append("sync")
            return {"browserClientSha256": "official-browser-hash"}

        with (
            mock.patch.dict(
                os.environ,
                {
                    "CODEX_APP_SERVER_FORCE_CLI": "1",
                    "CODEX_TPP_LOCAL_EXECUTOR_CLI_PATH": "/wrong/custom-executor",
                },
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.sync_primary_control_runtime",
                side_effect=sync_primary,
            ) as sync,
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.runtime_status",
                return_value={"ready": True},
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._wait_for_app_server",
                return_value=True,
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.subprocess.run",
                side_effect=lambda _command, check: events.append("launch"),
            ) as run,
            mock.patch("sudhir_codex_gateway.frontend_transition.time.sleep"),
        ):
            launch_primary(self.paths)

        self.assertEqual(events, ["sync", "launch"])
        sync.assert_called_once_with(self.paths)
        command = run.call_args.args[0]
        self.assertEqual(command.count("CODEX_APP_SERVER_FORCE_CLI=0"), 1)
        self.assertNotIn("CODEX_APP_SERVER_FORCE_CLI=1", command)
        executor_setting = (
            "CODEX_TPP_LOCAL_EXECUTOR_CLI_PATH="
            f"{self.app}/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
        )
        self.assertEqual(command.count(executor_setting), 1)
        self.assertNotIn(
            "CODEX_TPP_LOCAL_EXECUTOR_CLI_PATH=/wrong/custom-executor", command
        )
        self.assertIn(f"CODEX_HOME={self.primary}", command)
        self.assertIn(f"SUDHIR_CODEX_STATE={self.primary}", command)
        self.assertIn(f"SUDHIR_CODEX_GATEWAY_STATE={self.primary}", command)
        self.assertIn(
            f"CODEX_CLI_PATH={self.paths.installed_launcher}",
            command,
        )
        self.assertNotIn(f"CODEX_HOME={self.transition}", command)
        self.assertNotIn(f"SUDHIR_CODEX_STATE={self.transition}", command)
        control = self.paths.control_runtime
        self.assertIn(f"CODEX_NODE_REPL_PATH={control.primary_node_repl}", command)
        self.assertNotIn(f"CODEX_NODE_REPL_PATH={control.node_repl}", command)
        self.assertIn(f"CODEX_BROWSER_USE_NODE_PATH={control.node}", command)
        self.assertIn(f"--user-data-dir={self.profile}", command)
        self.assertIn("--remote-debugging-address=127.0.0.1", command)
        self.assertIn("--remote-debugging-port=0", command)
        self.assertNotIn("BUILD_FLAVOR=dev", command)

    def test_launch_primary_rejects_unusable_official_cloud_executor(self) -> None:
        executor = (
            self.app
            / "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
        )
        for failure in ("missing", "not executable"):
            with self.subTest(failure=failure):
                if failure == "missing":
                    executor.unlink()
                else:
                    executor.write_bytes(b"official-executor")
                with (
                    mock.patch(
                        "sudhir_codex_gateway.frontend_transition.os.access",
                        return_value=False,
                    ),
                    mock.patch.object(
                        frontend_transition, "sync_primary_control_runtime"
                    ) as sync,
                    mock.patch.object(frontend_transition, "runtime_status") as status,
                    mock.patch.object(frontend_transition.subprocess, "run") as run,
                ):
                    with self.assertRaisesRegex(
                        frontend_transition.TransitionError,
                        "Official cloud executor is missing or not executable",
                    ):
                        frontend_transition.launch_primary(self.paths)
                sync.assert_not_called()
                status.assert_not_called()
                run.assert_not_called()

    def test_ensure_activates_healthy_transition_without_relaunch(self) -> None:
        healthy = {
            "running": True,
            "appServerRunning": True,
            "primaryConfigUnchanged": True,
            "controlRuntime": {"ready": True},
        }
        with (
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.status",
                return_value=healthy,
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._official_main_processes",
                return_value=[],
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._activate_chatgpt"
            ) as activate,
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.launch"
            ) as launch_transition,
        ):
            result = ensure(self.paths)

        self.assertEqual(result["launcherAction"], "activated")
        self.assertFalse(result["primaryConfigDriftDetected"])
        activate.assert_called_once_with(self.paths)
        launch_transition.assert_not_called()

    def test_ensure_primary_activates_healthy_primary_home_without_relaunch(
        self,
    ) -> None:
        self.assertTrue(
            hasattr(frontend_transition, "ensure_primary"),
            "primary-home ensure mode is missing",
        )
        healthy = {
            "running": True,
            "appServerRunning": True,
            "primaryHomeActive": True,
        }
        with (
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.status_primary",
                return_value=healthy,
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._official_main_processes",
                return_value=[],
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._activate_chatgpt"
            ) as activate,
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.launch_primary"
            ) as launch_shared,
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.sync_primary_control_runtime"
            ) as sync_primary,
        ):
            result = frontend_transition.ensure_primary(self.paths)

        self.assertEqual(result["launcherAction"], "activated")
        activate.assert_called_once_with(self.paths)
        launch_shared.assert_not_called()
        sync_primary.assert_called_once_with(self.paths)

    def test_parser_exposes_primary_home_mode(self) -> None:
        parser = frontend_transition._parser()
        command_action = next(
            action for action in parser._actions if action.dest == "command"
        )
        self.assertIn("ensure-primary", command_action.choices)

    def test_ensure_stops_official_backend_and_launches_transition(self) -> None:
        unhealthy = {
            "running": False,
            "appServerRunning": False,
            "primaryConfigUnchanged": False,
            "controlRuntime": {"ready": True},
        }
        healthy = {
            "running": True,
            "appServerRunning": True,
            "primaryConfigUnchanged": False,
            "controlRuntime": {"ready": True},
        }
        official = [(101, 1, "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT")]
        with (
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.status",
                side_effect=(unhealthy, healthy),
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._official_main_processes",
                return_value=official,
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._transition_main_processes",
                return_value=[],
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._terminate_processes"
            ) as terminate,
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.launch"
            ) as launch_transition,
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._activate_chatgpt"
            ) as activate,
        ):
            result = ensure(self.paths)

        self.assertEqual(result["launcherAction"], "launched")
        self.assertTrue(result["primaryConfigDriftDetected"])
        terminate.assert_called_once_with(official)
        launch_transition.assert_called_once_with(self.paths)
        activate.assert_called_once_with(self.paths)

    def test_ensure_tolerates_primary_config_drift(self) -> None:
        healthy = {
            "running": True,
            "appServerRunning": True,
            "primaryConfigUnchanged": False,
            "controlRuntime": {"ready": True},
        }
        with (
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.status",
                return_value=healthy,
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._official_main_processes",
                return_value=[],
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._activate_chatgpt"
            ) as activate,
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.launch"
            ) as launch_transition,
        ):
            result = ensure(self.paths)

        self.assertEqual(result["launcherAction"], "activated")
        self.assertTrue(result["primaryConfigDriftDetected"])
        activate.assert_called_once_with(self.paths)
        launch_transition.assert_not_called()

    def test_control_wrapper_targets_copied_runtime(self) -> None:
        control = self.paths.control_runtime
        wrapper = _wrapper_text(control)

        self.assertIn(str(control.legacy_create_client), wrapper)
        self.assertIn(str(control.helper), wrapper)
        self.assertNotIn("/Applications/Sudhir-Codex.app", wrapper)
        self.assertIn("setupComputerUseRuntime", wrapper)

    def test_primary_wrapper_uses_matching_legacy_client_and_starts_helper(
        self,
    ) -> None:
        control = self.paths.control_runtime
        wrapper = _primary_wrapper_text(control)

        self.assertIn(str(control.legacy_create_client), wrapper)
        self.assertIn(str(control.helper), wrapper)
        self.assertIn("startConfiguredSudhirComputerUseService();", wrapper)
        self.assertNotIn("NODE_REPL_NODE_MODULE_DIRS", wrapper)

    def test_primary_node_repl_shim_overrides_poisoned_frontend_environment(
        self,
    ) -> None:
        self.assertTrue(
            hasattr(frontend_control, "_primary_node_repl_shim_text"),
            "stable primary node_repl shim is missing",
        )
        control = self.paths.control_runtime
        control.node_repl.parent.mkdir(parents=True)
        control.node_repl.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$CODEX_HOME\" \"$NODE_REPL_NODE_PATH\" "
            "\"$NODE_REPL_NODE_MODULE_DIRS\" \"$SKY_CUA_SERVICE_PATH\" "
            "\"$SKY_CUA_SERVICE_NATIVE_PIPE_PATH\" \"$SUDHIR_CUA_SERVICE_PATH\" "
            "\"$SUDHIR_CUA_SERVICE_NATIVE_PIPE_PATH\"\n",
            encoding="utf-8",
        )
        control.node_repl.chmod(0o700)
        control.primary_node_repl.parent.mkdir(parents=True)
        control.primary_node_repl.write_text(
            frontend_control._primary_node_repl_shim_text(control),
            encoding="utf-8",
        )
        control.primary_node_repl.chmod(0o700)
        poisoned = {
            **os.environ,
            "CODEX_HOME": "/tmp/wrong-codex-home",
            "NODE_REPL_NODE_PATH": "/tmp/wrong-node",
            "NODE_REPL_NODE_MODULE_DIRS": "/tmp/wrong-node-modules",
            "SKY_CUA_SERVICE_PATH": "/tmp/wrong-helper",
            "SKY_CUA_SERVICE_NATIVE_PIPE_PATH": "/tmp/wrong.sock",
            "SUDHIR_CUA_SERVICE_PATH": "/tmp/wrong-helper",
            "SUDHIR_CUA_SERVICE_NATIVE_PIPE_PATH": "/tmp/wrong.sock",
        }

        completed = subprocess.run(
            [str(control.primary_node_repl), "--probe"],
            check=True,
            capture_output=True,
            env=poisoned,
            text=True,
        )

        self.assertEqual(
            completed.stdout.splitlines(),
            [
                str(self.primary),
                str(control.node),
                str(control.official_node_modules),
                str(control.helper),
                str(control.socket),
                str(control.helper),
                str(control.socket),
            ],
        )

    @staticmethod
    def _instruction_source(marker: str) -> str:
        return (
            f"# Computer Use {marker}\n\n## Bootstrap\n"
            'globalThis.sky = (await import("@oai/sky")).sky;\n\n'
            f"## API surface\n{marker}: sky.list_apps()\n\n"
            f"## Policy\n{marker}: preserve confirmation rules.\n"
        )

    @staticmethod
    def _put_instruction(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _primary_control_case(self, name: str):
        control = replace(self.paths.control_runtime, primary_state=self.root / name)
        for executable in (control.node, control.node_repl):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o700)
        return control

    def _provision_primary_case(self, control):
        # Only platform signing/component checks are mocked; resolver and writes run.
        with (
            mock.patch.object(
                frontend_control, "_verify_control_components", return_value={}
            ),
            mock.patch.object(frontend_control, "_verify_openai_node_repl"),
        ):
            return frontend_control.provision_primary_control_runtime(control)

    @staticmethod
    def _plugin_case(state: Path, version: str) -> Path:
        return state / "plugins/cache/openai-bundled/computer-use" / version

    def _assert_primary_instructions(self, control, marker: str) -> bytes:
        content = control.primary_skill.read_bytes()
        text = content.decode("utf-8")
        self.assertIn(str(control.primary_wrapper), text)
        self.assertIn(f"{marker}: sky.list_apps()", text)
        self.assertIn(f"{marker}: preserve confirmation rules.", text)
        self.assertNotIn("<plugin root>", text)
        self.assertNotIn('import("@oai/sky")', text)
        for relative in frontend_control.PLUGIN_FILES[1:]:
            self.assertEqual(
                (
                    control.primary_state / "control/plugin-assets" / relative
                ).read_bytes(),
                content,
            )
        return content

    def test_primary_control_survives_plugin_layout_changes(self) -> None:
        for layout in ("no-cache", "missing-skill", "short-skill"):
            with self.subTest(layout=layout):
                control = self._primary_control_case(layout)
                self._put_instruction(
                    control.primary_skill, self._instruction_source("PINNED")
                )
                plugin = self._plugin_case(control.primary_state, "1.0.test")
                node_doc, skill = (
                    plugin / path for path in frontend_control.PLUGIN_FILES[1:]
                )
                if layout == "missing-skill":
                    self._put_instruction(
                        node_doc, self._instruction_source("UPSTREAM")
                    )
                elif layout == "short-skill":
                    self._put_instruction(skill, "# Short overview without Bootstrap\n")
                result = self._provision_primary_case(control)
                self.assertEqual(
                    result["controlPluginVersions"],
                    [] if layout == "no-cache" else ["1.0.test"],
                )
                content = self._assert_primary_instructions(control, "PINNED")
                for document, present in (
                    (node_doc, layout == "missing-skill"),
                    (skill, layout == "short-skill"),
                ):
                    self.assertEqual(document.is_file(), present)
                    if present:
                        self.assertEqual(document.read_bytes(), content)
                self._provision_primary_case(control)
                self.assertEqual(
                    self._assert_primary_instructions(control, "PINNED"), content
                )
                if layout == "missing-skill":
                    self.assertFalse(skill.exists())
                    self.assertFalse(
                        (
                            control.primary_state
                            / "control/original-plugins/1.0.test"
                            / frontend_control.PLUGIN_FILES[2]
                        ).exists()
                    )

    def test_primary_control_recovers_from_owned_templates(self) -> None:
        for folder in (
            "plugin-assets",
            "original-plugins/1.0.saved",
            "original-plugin-saved",
        ):
            for relative in frontend_control.PLUGIN_FILES[1:]:
                with self.subTest(folder=folder, relative=relative):
                    control = self._primary_control_case(
                        f"owned-{folder.replace('/', '-')}-{relative.name}"
                    )
                    source = control.primary_state / "control" / folder / relative
                    self._put_instruction(source, self._instruction_source("OWNED"))
                    wrong = (
                        control.primary_state
                        / "frontend-control-runtime"
                        / folder
                        / relative
                    )
                    self._put_instruction(wrong, self._instruction_source("WRONG-ROOT"))
                    plugin = self._plugin_case(control.primary_state, "9.0.new")
                    self._put_instruction(
                        plugin / relative, self._instruction_source("UPSTREAM")
                    )
                    self.assertEqual(
                        frontend_control._instruction_template(
                            control.primary_state,
                            frontend_control.PLUGIN_FILES[2],
                            control_root=control.primary_state / "control",
                        ),
                        source,
                    )
                    self._provision_primary_case(control)
                    self._assert_primary_instructions(control, "OWNED")

    def test_primary_control_recovers_from_node_instructions_and_cache_replacement(
        self,
    ) -> None:
        for short_skill in (False, True):
            with self.subTest(short_skill=short_skill):
                control = self._primary_control_case(f"cache-replacement-{short_skill}")
                old = self._plugin_case(control.primary_state, "1.0.old")
                node_relative, skill_relative = frontend_control.PLUGIN_FILES[1:]
                self._put_instruction(
                    old / node_relative, self._instruction_source("RECOVERED")
                )
                if short_skill:
                    self._put_instruction(old / skill_relative, "# Upstream overview\n")
                self._provision_primary_case(control)
                original = self._assert_primary_instructions(control, "RECOVERED")
                self.assertEqual((old / skill_relative).is_file(), short_skill)
                shutil.rmtree(
                    old
                )  # Simulate frontend pruning only this temporary cache.
                new = self._plugin_case(control.primary_state, "2.0.new")
                self._put_instruction(
                    new / node_relative, self._instruction_source("NEW-API")
                )
                result = self._provision_primary_case(control)
                self.assertEqual(result["controlPluginVersions"], ["2.0.new"])
                self.assertEqual(
                    self._assert_primary_instructions(control, "RECOVERED"), original
                )
                self.assertEqual((new / node_relative).read_bytes(), original)
                self.assertFalse((new / skill_relative).exists())

    def test_transition_template_recovery_uses_transition_backups(self) -> None:
        for folder in ("original-plugins/1.0.saved", "original-plugin-saved"):
            with self.subTest(folder=folder):
                state = self.root / ("legacy-" + folder.replace("/", "-"))
                relative = frontend_control.PLUGIN_FILES[2]
                source = state / "frontend-control-runtime" / folder / relative
                self._put_instruction(source, self._instruction_source("TRANSITION"))
                self._put_instruction(
                    state / "control" / folder / relative,
                    self._instruction_source("WRONG-PRIMARY"),
                )
                plugin = self._plugin_case(state, "9.0.new")
                self._put_instruction(
                    plugin / relative, self._instruction_source("UPSTREAM")
                )
                self.assertEqual(
                    frontend_control._instruction_template(state, relative), source
                )
                assets = frontend_control._prepare_plugin_assets(
                    self.paths.control_runtime, state
                )
                text = assets.skill.read_text(encoding="utf-8")
                self.assertIn("TRANSITION: sky.list_apps()", text)
                self.assertIn("<plugin root>/scripts/computer-use-client.mjs", text)
                self.assertNotIn(str(self.paths.control_runtime.primary_wrapper), text)

    def test_primary_control_reports_missing_instruction_sources(self) -> None:
        control = self._primary_control_case("no-source")
        plugin = self._plugin_case(control.primary_state, "1.0.short")
        self._put_instruction(plugin / frontend_control.PLUGIN_FILES[2], "# Overview\n")
        with self.assertRaisesRegex(
            frontend_control.ControlRuntimeError, "no recoverable instruction template"
        ):
            self._provision_primary_case(control)

    def test_primary_local_skill_uses_stable_wrapper_outside_plugin_cache(
        self,
    ) -> None:
        self.assertTrue(
            hasattr(frontend_control, "_rewrite_primary_skill"),
            "stable primary Computer Use skill rewrite is missing",
        )
        source = """---
name: computer-use
---

## Bootstrap

```js
globalThis.sky = (await import("@oai/sky")).sky;
```

## API surface

Keep this API text.
"""
        control = self.paths.control_runtime

        updated = frontend_control._rewrite_primary_skill(source, control)

        self.assertIn(str(control.primary_wrapper), updated)
        self.assertNotIn("<plugin root>", updated)
        self.assertNotIn('import("@oai/sky")', updated)
        self.assertIn("Keep this API text.", updated)

    def test_primary_control_config_points_to_stable_node_repl_shim(self) -> None:
        control = self.paths.control_runtime

        updated = apply_primary_control_config(
            (self.primary / "config.toml").read_text(encoding="utf-8"),
            control,
            official_version="26.new",
            browser_client_hash="fresh-browser-hash",
        )

        self.assertIn(f'command = "{control.primary_node_repl}"', updated)
        self.assertNotIn(f'command = "{control.node_repl}"', updated)

    def test_primary_control_config_restores_helper_socket_and_primary_home(
        self,
    ) -> None:
        config_path = self.primary / "config.toml"
        source = config_path.read_text(encoding="utf-8")
        source = source.replace(
            str(self.primary / "control" / "Sudhir Computer Use.app"),
            str(self.primary / "computer-use" / "Codex Computer Use.app"),
        ).replace(
            f'SKY_CUA_SERVICE_NATIVE_PIPE_PATH = "{self.primary}/control/run/computer-use.sock"\n',
            "",
        )
        control = self.paths.control_runtime

        updated = apply_primary_control_config(
            source,
            control,
            official_version="26.new",
            browser_client_hash="fresh-browser-hash",
        )

        self.assertIn(f'command = "{control.primary_node_repl}"', updated)
        self.assertIn(
            f'NODE_REPL_NODE_MODULE_DIRS = "{control.official_node_modules}"',
            updated,
        )
        self.assertIn(f'NODE_REPL_NODE_PATH = "{control.node}"', updated)
        self.assertIn(f'CODEX_HOME = "{self.primary}"', updated)
        self.assertNotIn(f'CODEX_HOME = "{self.transition}"', updated)
        self.assertIn(f'SKY_CUA_SERVICE_PATH = "{control.helper}"', updated)
        self.assertIn(
            f'SKY_CUA_SERVICE_NATIVE_PIPE_PATH = "{control.socket}"',
            updated,
        )
        self.assertIn(f'SUDHIR_CUA_SERVICE_PATH = "{control.helper}"', updated)
        self.assertIn(
            f'SUDHIR_CUA_SERVICE_NATIVE_PIPE_PATH = "{control.socket}"',
            updated,
        )
        self.assertEqual(updated.count("fresh-browser-hash"), 2)
        self.assertIn('BROWSER_USE_CODEX_APP_VERSION = "26.new"', updated)

    def test_primary_control_config_recreates_frontend_removed_trust_entries(
        self,
    ) -> None:
        source = (self.primary / "config.toml").read_text(encoding="utf-8")
        source = source.replace(
            'NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "old-browser-hash"\n',
            "",
            1,
        ).replace(
            "[shell_environment_policy.set]\n"
            f'NODE_REPL_TRUSTED_CODE_PATHS = "{self.primary}"\n',
            "[shell_environment_policy.set]\n",
            1,
        )
        control = self.paths.control_runtime

        updated = apply_primary_control_config(
            source,
            control,
            official_version="26.new",
            browser_client_hash="fresh-browser-hash",
        )

        trusted_paths = f"{self.primary}:{control.official_node_modules}"
        self.assertEqual(
            updated.count(f'NODE_REPL_TRUSTED_CODE_PATHS = "{trusted_paths}"'),
            2,
        )
        self.assertEqual(
            updated.count(
                'NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "fresh-browser-hash"'
            ),
            2,
        )

    def test_native_pipe_rewrite_uses_transition_specific_environment(self) -> None:
        source = (
            'const a="SKY_CUA_SERVICE_PATH";const b="SKY_CUA_SERVICE_NATIVE_PIPE_PATH";'
        )
        updated = _rewrite_native_pipe_source(source)

        self.assertIn("SUDHIR_CUA_SERVICE_PATH", updated)
        self.assertIn("SUDHIR_CUA_SERVICE_NATIVE_PIPE_PATH", updated)
        self.assertNotIn('"SKY_CUA_SERVICE_PATH"', updated)
        self.assertNotIn('"SKY_CUA_SERVICE_NATIVE_PIPE_PATH"', updated)
        self.assertEqual(_rewrite_native_pipe_source(updated), updated)

    def test_instruction_rewrite_preserves_policy(self) -> None:
        source = """---
name: computer-use
---

## Bootstrap

```js
globalThis.sky = (await import("@oai/sky")).sky;
```

## API surface

Keep this API text.

# Computer Use Confirmations Policy

Keep this policy text.
"""
        updated = _rewrite_instructions(source)

        self.assertIn("setupComputerUseRuntime", updated)
        self.assertNotIn('import("@oai/sky")', updated)
        self.assertIn("Keep this API text.", updated)
        self.assertIn("Keep this policy text.", updated)

    def test_sync_refreshes_config_and_metadata(self) -> None:
        self.transition.mkdir()
        self.profile.mkdir()
        browser_hash = hashlib.sha256(b"browser-client").hexdigest()
        rewritten = rewrite_transition_config(
            (self.primary / "config.toml").read_text(),
            primary_state=self.primary,
            transition_state=self.transition,
            official_app=self.app,
            official_version="26.old",
            browser_client_hash="old-hash",
            legacy_cua_source=self.legacy_cua_source,
        )
        (self.transition / "config.toml").write_text(rewritten)
        (self.transition / TRANSITION_METADATA).write_text(
            json.dumps(
                {
                    "officialVersion": "26.old",
                    "browserClientSha256": "old-hash",
                }
            )
        )

        with (
            mock.patch(
                "sudhir_codex_gateway.frontend_transition._verify_official_resources",
                return_value=("com.openai.codex", "26.new", "5678", browser_hash),
            ),
            mock.patch(
                "sudhir_codex_gateway.frontend_transition.provision_control_runtime",
                return_value={"controlNodeReplSha256": "fresh-runtime"},
            ),
        ):
            result = sync_control_runtime(self.paths)

        self.assertEqual(result["controlNodeReplSha256"], "fresh-runtime")
        config = (self.transition / "config.toml").read_text()
        self.assertIn("26.new", config)
        self.assertEqual(config.count(browser_hash), 2)
        metadata = json.loads((self.transition / TRANSITION_METADATA).read_text())
        self.assertEqual(metadata["officialVersion"], "26.new")
        self.assertEqual(metadata["officialBuild"], "5678")
        self.assertEqual(metadata["legacyCuaSource"], str(self.legacy_cua_source))
        self.assertEqual(metadata["controlNodeReplSha256"], "fresh-runtime")

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
