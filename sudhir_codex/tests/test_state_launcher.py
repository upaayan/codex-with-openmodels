import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sudhir_codex_gateway.app import GATEWAY_TOKEN_HEADER
from sudhir_codex_gateway.errors import GatewayError
from sudhir_codex_gateway.launcher import _forced_config
from sudhir_codex_gateway.launcher import _reject_critical_overrides
from sudhir_codex_gateway.launcher import main as launcher_main
from sudhir_codex_gateway.management import start_gateway
from sudhir_codex_gateway.management import stop_gateway
from sudhir_codex_gateway.platform_support import private_access_label
from sudhir_codex_gateway.state import MCP_BEGIN
from sudhir_codex_gateway.state import RuntimePaths
from sudhir_codex_gateway.state import ensure_private_state
from sudhir_codex_gateway.state import import_official_auth
from sudhir_codex_gateway.state import import_official_mcp
from sudhir_codex_gateway.state import validate_isolation


class StateAndLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.official = self.root / "official-codex"
        self.official.mkdir()
        self.pi = self.root / "pi"
        self.pi.mkdir()
        self.paths = RuntimePaths(
            repo_root=self.repo,
            state_dir=self.root / "private-codex",
            pi_agent_dir=self.pi,
            official_codex_home_override=self.official,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_private_access(self, path: Path, unix_mode: int) -> None:
        expected = "windows-acl" if os.name == "nt" else oct(unix_mode)
        self.assertEqual(private_access_label(path), expected)

    def test_private_state_is_independent_and_mode_restricted(self) -> None:
        token = ensure_private_state(self.paths)

        self.assertGreaterEqual(len(token), 32)
        self.assertNotEqual(
            self.paths.state_dir.resolve(),
            self.official.resolve(),
        )
        self.assert_private_access(self.paths.state_dir, 0o700)
        self.assert_private_access(self.paths.gateway_dir, 0o700)
        self.assert_private_access(self.paths.token_file, 0o600)
        self.assert_private_access(self.paths.config_file, 0o600)
        config = self.paths.config_file.read_text(encoding="utf-8")
        self.assertIn("max_concurrent_threads_per_session = 6", config)
        self.assertIn('tool_namespace = "sudhir_agents"', config)
        self.assertIn("enable_request_compression = false", config)
        self.assertIn('exporter = "none"', config)
        self.assertIn(
            'SUDHIR_CODEX_GATEWAY_TOKEN = "exclude"',
            config,
        )

    def test_existing_private_config_migrates_agent_namespace(self) -> None:
        self.paths.state_dir.mkdir()
        self.paths.config_file.write_text(
            """
[features.multi_agent_v2]
tool_namespace = "collaboration"
wait_agent_enabled = true

[mcp_servers.demo]
command = "demo-mcp"
""".lstrip(),
            encoding="utf-8",
        )

        ensure_private_state(self.paths)

        config = self.paths.config_file.read_text(encoding="utf-8")
        self.assertIn('tool_namespace = "sudhir_agents"', config)
        self.assertNotIn('tool_namespace = "collaboration"', config)
        self.assertIn("wait_agent_enabled = true", config)
        self.assertIn("[mcp_servers.demo]", config)
        self.assertIn('command = "demo-mcp"', config)
        self.assertIn(
            'SUDHIR_CODEX_GATEWAY_TOKEN = "exclude"',
            config,
        )

    def test_existing_gateway_token_filter_is_forced_to_exclude(self) -> None:
        self.paths.state_dir.mkdir()
        self.paths.config_file.write_text(
            """
[shell_environment_policy.filters]
SUDHIR_CODEX_GATEWAY_TOKEN = "include"
KEEP_ME = "include"

[mcp_servers.demo]
command = "demo-mcp"
""".lstrip(),
            encoding="utf-8",
        )

        ensure_private_state(self.paths)

        config = self.paths.config_file.read_text(encoding="utf-8")
        self.assertIn(
            'SUDHIR_CODEX_GATEWAY_TOKEN = "exclude"',
            config,
        )
        self.assertNotIn(
            'SUDHIR_CODEX_GATEWAY_TOKEN = "include"',
            config,
        )
        self.assertIn('KEEP_ME = "include"', config)
        self.assertIn("[mcp_servers.demo]", config)

    def test_existing_legacy_shell_excludes_are_preserved(self) -> None:
        self.paths.state_dir.mkdir()
        self.paths.config_file.write_text(
            """
[shell_environment_policy]
exclude = ["KEEP_OUT"]

[mcp_servers.demo]
command = "demo-mcp"
""".lstrip(),
            encoding="utf-8",
        )

        ensure_private_state(self.paths)

        config = self.paths.config_file.read_text(encoding="utf-8")
        self.assertIn(
            'exclude = ["KEEP_OUT", "SUDHIR_CODEX_GATEWAY_TOKEN"]',
            config,
        )
        self.assertIn("[mcp_servers.demo]", config)

    def test_auth_import_copies_then_backs_up_without_linking(self) -> None:
        official_document = {
            "tokens": {
                "access_token": "official-access",
                "refresh_token": "official-refresh",
            }
        }
        self.paths.official_auth_file.write_text(
            json.dumps(official_document),
            encoding="utf-8",
        )
        os.chmod(self.paths.official_auth_file, 0o600)

        first_backup = import_official_auth(self.paths)
        self.assertIsNone(first_backup)
        self.assertFalse(self.paths.private_auth_file.is_symlink())
        self.assertEqual(
            json.loads(self.paths.private_auth_file.read_text()),
            official_document,
        )

        self.paths.private_auth_file.write_text(
            '{"tokens":{"access_token":"private-old"}}',
            encoding="utf-8",
        )
        second_backup = import_official_auth(self.paths)
        self.assertIsNotNone(second_backup)
        self.assertTrue(second_backup.is_file())
        self.assertIn("private-old", second_backup.read_text())
        self.assert_private_access(self.paths.private_auth_file, 0o600)

    def test_mcp_import_copies_only_mcp_server_tables(self) -> None:
        (self.official / "config.toml").write_text(
            """
model_provider = "must-not-copy"

[analytics]
enabled = true

[mcp_servers.local]
command = "example-mcp"
args = ["--safe"]

[mcp_servers.remote]
url = "https://mcp.test/service"

[mcp_servers.remote.http_headers]
X-Test = "value"
""".strip()
            + "\n",
            encoding="utf-8",
        )

        count = import_official_mcp(self.paths)
        private = self.paths.config_file.read_text(encoding="utf-8")

        self.assertEqual(count, 2)
        self.assertEqual(private.count(MCP_BEGIN), 1)
        self.assertIn("[mcp_servers.local]", private)
        self.assertIn('command = "example-mcp"', private)
        self.assertIn("[mcp_servers.remote.http_headers]", private)
        self.assertNotIn("must-not-copy", private)
        self.assertNotIn("[analytics]\nenabled = true", private)
        self.assertIn("enable_request_compression = false", private)

        self.assertEqual(import_official_mcp(self.paths), 2)
        private_again = self.paths.config_file.read_text(encoding="utf-8")
        self.assertEqual(private_again.count(MCP_BEGIN), 1)

    def test_official_or_symlinked_state_is_rejected(self) -> None:
        official_paths = RuntimePaths(
            repo_root=self.repo,
            state_dir=self.official,
            pi_agent_dir=self.pi,
            official_codex_home_override=self.official,
        )
        with self.assertRaisesRegex(GatewayError, "official"):
            validate_isolation(official_paths)

        symlink_target = self.root / "private-target"
        symlink_target.mkdir()
        symlink_state = self.root / "private-link"
        symlink_state.symlink_to(symlink_target, target_is_directory=True)
        linked_paths = RuntimePaths(
            repo_root=self.repo,
            state_dir=symlink_state,
            pi_agent_dir=self.pi,
            official_codex_home_override=self.official,
        )
        with self.assertRaisesRegex(GatewayError, "symlink"):
            validate_isolation(linked_paths)

    def test_launcher_rejects_security_critical_overrides(self) -> None:
        rejected = [
            ["-c", 'model_provider="other"'],
            ['-cmodel_provider="other"'],
            ['-c=model_provider="other"'],
            ["--config=otel.exporter=\"http\""],
            ["--enable", "enable_request_compression"],
            ["--enable=enable_request_compression"],
            ["--disable=enable_request_compression"],
            ["-c", "agents.max_concurrent_threads_per_session=1"],
            ["-c", 'features.multi_agent_v2.tool_namespace="collaboration"'],
            ["-c", 'shell_environment_policy.inherit="all"'],
            ["--oss"],
            ["exec", "--oss", "prompt"],
            ["--local-provider", "ollama"],
            ["exec", "--local-provider=lmstudio", "prompt"],
        ]
        for argv in rejected:
            with self.subTest(argv=argv):
                with self.assertRaises(GatewayError):
                    _reject_critical_overrides(argv)

        _reject_critical_overrides(["-c", 'model="pi-demo/demo/model"'])
        _reject_critical_overrides(['-cmodel="pi-demo/demo/model"'])

    def test_forced_config_pins_gateway_and_telemetry_off(self) -> None:
        argv = _forced_config("http://127.0.0.1:32179")
        joined = "\n".join(argv)

        self.assertIn('model_provider="sudhir_gateway"', joined)
        self.assertIn(
            f'"{GATEWAY_TOKEN_HEADER}" = "SUDHIR_CODEX_GATEWAY_TOKEN"',
            joined,
        )
        self.assertIn("analytics.enabled=false", joined)
        self.assertIn('otel.exporter="none"', joined)
        self.assertIn("features.enable_request_compression=false", joined)
        self.assertIn("agents.max_concurrent_threads_per_session=6", joined)
        self.assertIn(
            'features.multi_agent_v2.tool_namespace="sudhir_agents"',
            joined,
        )
        self.assertIn(
            'shell_environment_policy.filters.SUDHIR_CODEX_GATEWAY_TOKEN="exclude"',
            joined,
        )

    def test_windows_runtime_paths_use_native_names(self) -> None:
        with mock.patch("sudhir_codex_gateway.platform_support.WINDOWS", True):
            self.assertEqual(
                self.paths.core_binary,
                self.repo / "dist" / "sudhir-codex-core.exe",
            )
            self.assertEqual(
                self.paths.venv_python,
                self.repo / ".venv" / "Scripts" / "python.exe",
            )
            self.assertEqual(
                self.paths.installed_launcher,
                self.repo / "bin" / "sudhir-codex.cmd",
            )

    def test_windows_launcher_waits_for_core_and_returns_its_status(self) -> None:
        with mock.patch("sudhir_codex_gateway.platform_support.WINDOWS", True):
            windows_core = self.paths.core_binary
            windows_core.parent.mkdir(parents=True)
            windows_core.touch()
            with (
                mock.patch(
                    "sudhir_codex_gateway.launcher.runtime_paths_from_env",
                    return_value=self.paths,
                ),
                mock.patch(
                    "sudhir_codex_gateway.launcher.ensure_private_state",
                    return_value="private-token",
                ),
                mock.patch("sudhir_codex_gateway.launcher.start_gateway"),
                mock.patch(
                    "sudhir_codex_gateway.launcher.is_windows",
                    return_value=True,
                ),
                mock.patch(
                    "sudhir_codex_gateway.launcher.subprocess.run"
                ) as run_core,
            ):
                run_core.return_value.returncode = 7

                result = launcher_main(["--version"])

        self.assertEqual(result, 7)
        command = run_core.call_args.args[0]
        self.assertEqual(command[0], str(windows_core))
        self.assertEqual(command[-1], "--version")
        self.assertEqual(
            run_core.call_args.kwargs["env"]["SUDHIR_CODEX_GATEWAY_TOKEN"],
            "private-token",
        )

    def test_gateway_start_is_serialized_by_private_lock(self) -> None:
        python = self.paths.venv_python
        python.parent.mkdir(parents=True)
        python.touch()
        winner = {
            "running": True,
            "pid": 4321,
            "process_alive": True,
            "health": {"service": "sudhir-codex-gateway"},
        }
        with (
            mock.patch(
                "sudhir_codex_gateway.management.gateway_status",
                side_effect=[
                    {
                        "running": False,
                        "pid": None,
                        "process_alive": False,
                        "health": None,
                    },
                    winner,
                ],
            ),
            mock.patch("sudhir_codex_gateway.management.subprocess.Popen") as popen,
            mock.patch(
                "sudhir_codex_gateway.platform_support._restrict_windows_acl"
            ),
        ):
            process = popen.return_value
            process.pid = 4321
            process.poll.return_value = None

            self.assertEqual(start_gateway(self.paths), 4321)

        self.assert_private_access(self.paths.start_lock_file, 0o600)

    def test_windows_stop_uses_private_health_check_not_ps(self) -> None:
        self.paths.gateway_dir.mkdir(parents=True)
        self.paths.pid_file.write_text("4321\n", encoding="ascii")
        with (
            mock.patch(
                "sudhir_codex_gateway.management._process_alive",
                side_effect=[True, False],
            ),
            mock.patch(
                "sudhir_codex_gateway.management.gateway_status",
                return_value={
                    "running": True,
                    "pid": 4321,
                    "process_alive": True,
                    "health": {
                        "service": "sudhir-codex-gateway",
                        "pid": 4321,
                    },
                },
            ),
            mock.patch(
                "sudhir_codex_gateway.management.is_windows",
                return_value=True,
            ),
            mock.patch(
                "sudhir_codex_gateway.management.terminate_process"
            ) as terminate,
            mock.patch(
                "sudhir_codex_gateway.management._process_command"
            ) as process_command,
        ):
            self.assertTrue(stop_gateway(self.paths))

        terminate.assert_called_once_with(4321)
        process_command.assert_not_called()

    def test_windows_stop_refuses_health_from_another_pid(self) -> None:
        self.paths.gateway_dir.mkdir(parents=True)
        self.paths.pid_file.write_text("4321\n", encoding="ascii")
        with (
            mock.patch(
                "sudhir_codex_gateway.management._process_alive",
                return_value=True,
            ),
            mock.patch(
                "sudhir_codex_gateway.management.gateway_status",
                return_value={
                    "running": False,
                    "pid": 4321,
                    "process_alive": True,
                    "health": {
                        "service": "sudhir-codex-gateway",
                        "pid": 9876,
                    },
                },
            ),
            mock.patch(
                "sudhir_codex_gateway.management.is_windows",
                return_value=True,
            ),
            mock.patch(
                "sudhir_codex_gateway.management.terminate_process"
            ) as terminate,
        ):
            with self.assertRaisesRegex(GatewayError, "failed the private health"):
                stop_gateway(self.paths)

        terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
