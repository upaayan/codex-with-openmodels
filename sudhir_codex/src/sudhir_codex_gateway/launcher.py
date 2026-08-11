"""Guarded launcher that makes the forked core use only private state."""

import os
import subprocess
import sys
from pathlib import Path

from .app import GATEWAY_TOKEN_HEADER
from .errors import GatewayError
from .management import main as management_main
from .management import runtime_paths_from_env
from .management import start_gateway
from .platform_support import is_windows
from .state import RuntimePaths
from .state import ensure_private_state

GATEWAY_STATE_ENV = "SUDHIR_CODEX_GATEWAY_STATE"


CRITICAL_CONFIG_PREFIXES = (
    "agents",
    "analytics",
    "check_for_update_on_startup",
    "cli_auth_credentials_store",
    "feedback",
    "features.enable_request_compression",
    "model_provider",
    "model_providers.sudhir_gateway",
    "features.multi_agent_v2.tool_namespace",
    "otel",
    "shell_environment_policy",
)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    paths = runtime_paths_from_env()
    try:
        token = ensure_private_state(paths)
        gateway_paths = _gateway_paths_from_env(paths)
        if gateway_paths != paths:
            token = ensure_private_state(gateway_paths)
        management = _management_command(argv)
        if management is not None:
            return management_main(management)
        _reject_critical_overrides(argv)
        if not paths.core_binary.is_file():
            raise GatewayError(
                69,
                "core_binary_missing",
                f"Fork core is missing at {paths.core_binary}; run the installer",
            )
        start_gateway(gateway_paths)
        environment = os.environ.copy()
        environment.update(
            {
                "CODEX_HOME": str(paths.state_dir.resolve()),
                "SUDHIR_CODEX_ROOT": str(paths.repo_root.resolve()),
                "SUDHIR_CODEX_STATE": str(paths.state_dir.resolve()),
                "SUDHIR_CODEX_PI_AGENT_DIR": str(paths.pi_agent_dir.resolve()),
                "SUDHIR_CODEX_GATEWAY_TOKEN": token,
                "SUDHIR_CODEX_LAUNCHER": "1",
            }
        )
        forced = _forced_config(gateway_paths.gateway_url)
        if is_windows():
            environment.setdefault(
                "HOME",
                os.environ.get("USERPROFILE") or str(Path.home()),
            )
            try:
                completed = subprocess.run(
                    [str(paths.core_binary), *forced, *argv],
                    check=False,
                    env=environment,
                )
            except OSError as exc:
                raise GatewayError(
                    69,
                    "core_launch_failed",
                    f"Fork core could not be launched at {paths.core_binary}",
                ) from exc
            return completed.returncode
        os.execve(
            paths.core_binary,
            [str(paths.core_binary), *forced, *argv],
            environment,
        )
    except GatewayError as exc:
        print(f"sudhir-codex: {exc.message}", file=sys.stderr)
        return exc.status if 1 <= exc.status <= 125 else 1
    return 1


def _gateway_paths_from_env(paths: RuntimePaths) -> RuntimePaths:
    raw_state = os.environ.get(GATEWAY_STATE_ENV)
    if raw_state is None or not raw_state.strip():
        return paths
    return RuntimePaths(
        repo_root=paths.repo_root,
        state_dir=Path(raw_state).expanduser(),
        pi_agent_dir=paths.pi_agent_dir,
        official_codex_home_override=paths.official_codex_home_override,
    )


def _management_command(argv: list[str]) -> list[str] | None:
    if not argv:
        return None
    if argv[0] in {"models", "doctor"}:
        return argv
    if argv[:2] == ["auth", "import"]:
        return ["auth-import", *argv[2:]]
    if argv[:2] == ["mcp", "import"]:
        return ["mcp-import", *argv[2:]]
    if argv[0] == "gateway":
        if len(argv) < 2:
            raise GatewayError(
                64,
                "gateway_command_missing",
                "Use gateway start, stop, status, or rotate-token",
            )
        command = argv[1]
        if command not in {"start", "stop", "status", "rotate-token"}:
            raise GatewayError(
                64,
                "gateway_command_invalid",
                "Use gateway start, stop, status, or rotate-token",
            )
        return [command, *argv[2:]]
    if argv[:2] == ["uninstall", "--state"]:
        raise GatewayError(
            64,
            "manual_uninstall_only",
            (
                "Uninstall is intentionally manual; see sudhir_codex/README.md "
                "for the exact private paths"
            ),
        )
    return None


def _reject_critical_overrides(argv: list[str]) -> None:
    trusted_node_repl_sandbox = _is_node_repl_sandbox_invocation(argv)
    index = 0
    while index < len(argv):
        argument = argv[index]
        if (
            argument == "--oss"
            or argument.startswith("--oss=")
            or argument == "--local-provider"
            or argument.startswith("--local-provider=")
        ):
            raise GatewayError(
                64,
                "provider_override_rejected",
                "Launcher refuses local-provider flags; choose a merged-catalog model",
            )
        override: str | None = None
        if argument in {"-c", "--config"}:
            if index + 1 >= len(argv):
                return
            override = argv[index + 1]
            index += 1
        elif argument.startswith("--config="):
            override = argument.split("=", 1)[1]
        elif argument.startswith("-c") and not argument.startswith("--"):
            override = argument[2:].removeprefix("=")
        if override:
            key = override.split("=", 1)[0].strip()
            if any(
                key == prefix or key.startswith(f"{prefix}.")
                for prefix in CRITICAL_CONFIG_PREFIXES
            ):
                if (
                    key == "shell_environment_policy.inherit"
                    and "=" in override
                    and override.split("=", 1)[1].strip() == '"all"'
                    and trusted_node_repl_sandbox
                ):
                    index += 1
                    continue
                raise GatewayError(
                    64,
                    "critical_override_rejected",
                    f"Launcher refuses security-critical config override {key!r}",
                )
        feature: str | None = None
        if argument in {"--enable", "--disable"} and index + 1 < len(argv):
            feature = argv[index + 1]
            index += 1
        elif argument.startswith("--enable=") or argument.startswith("--disable="):
            feature = argument.split("=", 1)[1]
        if feature is not None:
            if feature == "enable_request_compression":
                raise GatewayError(
                    64,
                    "compression_override_rejected",
                    "Gateway request compression must remain disabled",
                )
        index += 1


def _is_node_repl_sandbox_invocation(argv: list[str]) -> bool:
    """Recognize the sandbox profile emitted by the bundled node_repl runtime.

    Inheriting the parent environment is safe for this one profile because the
    launcher still supplies its forced filter for SUDHIR_CODEX_GATEWAY_TOKEN and
    continues to reject every caller-supplied filter/set/include/exclude policy.
    """
    if not argv or argv[0] != "sandbox":
        return False

    overrides: list[str] = []
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"-c", "--config"}:
            if index + 1 >= len(argv):
                return False
            overrides.append(argv[index + 1])
            index += 2
            continue
        if argument.startswith("--config="):
            overrides.append(argument.split("=", 1)[1])
        elif argument.startswith("-c") and not argument.startswith("--"):
            overrides.append(argument[2:].removeprefix("="))
        index += 1

    parsed = [
        tuple(part.strip() for part in override.split("=", 1))
        for override in overrides
        if "=" in override
    ]
    return ("default_permissions", '"node_repl"') in parsed and any(
        key == "permissions.node_repl" for key, _value in parsed
    )


def _forced_config(gateway_url: str) -> list[str]:
    provider = (
        '{ name = "Sudhir Gateway", '
        f'base_url = "{gateway_url}/v1", '
        'wire_api = "responses", requires_openai_auth = true, '
        "supports_websockets = false, supports_standalone_web_search = true, "
        'env_http_headers = { "'
        + GATEWAY_TOKEN_HEADER
        + '" = "SUDHIR_CODEX_GATEWAY_TOKEN" } }'
    )
    values = [
        'model_provider="sudhir_gateway"',
        'web_search="live"',
        f"model_providers.sudhir_gateway={provider}",
        "analytics.enabled=false",
        "feedback.enabled=false",
        'otel.exporter="none"',
        'otel.trace_exporter="none"',
        'otel.metrics_exporter="none"',
        "otel.log_user_prompt=false",
        "check_for_update_on_startup=false",
        'cli_auth_credentials_store="file"',
        "features.enable_request_compression=false",
        "features.standalone_web_search=true",
        "agents.enabled=true",
        "agents.max_concurrent_threads_per_session=6",
        'features.multi_agent_v2.tool_namespace="sudhir_agents"',
        ('shell_environment_policy.filters.SUDHIR_CODEX_GATEWAY_TOKEN="exclude"'),
    ]
    result: list[str] = []
    for value in values:
        result.extend(("-c", value))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
