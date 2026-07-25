"""Lifecycle and diagnostics commands for the private gateway."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from .app import GATEWAY_TOKEN_HEADER
from .app import CHATGPT_CLIENT_VERSION
from .app import GatewaySettings
from .errors import GatewayError
from .platform_support import detached_process_kwargs
from .platform_support import ensure_private_directory
from .platform_support import ensure_private_file
from .platform_support import gateway_start_lock
from .platform_support import is_windows
from .platform_support import platform_scope
from .platform_support import process_alive as _process_alive
from .platform_support import terminate_process
from .server import DEFAULT_PORT
from .server import run_server
from .state import RuntimePaths
from .state import ensure_private_state
from .state import file_mode
from .state import import_official_auth
from .state import import_official_mcp
from .state import rotate_gateway_token
from .state import validate_isolation


def runtime_paths_from_env() -> RuntimePaths:
    repo_root = Path(
        os.environ.get(
            "SUDHIR_CODEX_ROOT",
            str(Path.home() / ".playground" / "sudhir-codex"),
        )
    ).expanduser()
    state_dir = Path(
        os.environ.get("SUDHIR_CODEX_STATE", str(Path.home() / ".sudhir-codex"))
    ).expanduser()
    pi_agent_dir = Path(
        os.environ.get("SUDHIR_CODEX_PI_AGENT_DIR", str(Path.home() / ".pi" / "agent"))
    ).expanduser()
    return RuntimePaths(
        repo_root=repo_root,
        state_dir=state_dir,
        pi_agent_dir=pi_agent_dir,
    )


def gateway_settings(paths: RuntimePaths) -> GatewaySettings:
    token = ensure_private_state(paths)
    return GatewaySettings(
        repo_root=paths.repo_root,
        state_dir=paths.state_dir,
        pi_agent_dir=paths.pi_agent_dir,
        gateway_token=token,
    )


def gateway_status(paths: RuntimePaths) -> dict[str, Any]:
    validate_isolation(paths)
    pid = _read_pid(paths.pid_file)
    process_alive = bool(pid and _process_alive(pid))
    token = None
    try:
        token = paths.token_file.read_text(encoding="ascii").strip()
    except OSError:
        pass
    health: dict[str, Any] | None = None
    if token:
        try:
            response = httpx.get(
                f"{paths.gateway_url}/healthz",
                headers={GATEWAY_TOKEN_HEADER: token},
                timeout=0.5,
                follow_redirects=False,
            )
            if response.status_code == 200:
                value = response.json()
                if isinstance(value, dict):
                    health = value
        except (httpx.HTTPError, ValueError):
            pass
    expected_instance = None
    if token:
        expected_instance = gateway_settings(paths).instance_id
    healthy = bool(
        process_alive
        and health
        and health.get("service") == "sudhir-codex-gateway"
        and health.get("instance_id") == expected_instance
        and health.get("pid") == pid
    )
    return {
        "running": healthy,
        "pid": pid,
        "process_alive": process_alive,
        "health": health,
    }


def start_gateway(paths: RuntimePaths, *, timeout_seconds: float = 10.0) -> int:
    settings = gateway_settings(paths)
    ensure_private_directory(paths.gateway_dir)
    with gateway_start_lock(paths.start_lock_file):
        return _start_gateway_locked(paths, settings, timeout_seconds)


def _start_gateway_locked(
    paths: RuntimePaths,
    settings: GatewaySettings,
    timeout_seconds: float,
) -> int:
    status = gateway_status(paths)
    if status["running"]:
        return int(status["pid"])
    existing_pid = status.get("pid")
    if existing_pid and status.get("process_alive"):
        raise GatewayError(
            73,
            "gateway_pid_conflict",
            "Gateway PID belongs to a live process that failed the private health check",
        )
    if paths.pid_file.exists():
        paths.pid_file.unlink()
    if not paths.venv_python.is_file():
        raise GatewayError(
            69,
            "python_runtime_missing",
            f"Private Python runtime is missing at {paths.venv_python}",
        )

    ensure_private_directory(paths.gateway_dir)
    paths.gateway_log.touch(mode=0o600, exist_ok=True)
    ensure_private_file(paths.gateway_log)
    log_fd = os.open(
        paths.gateway_log,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    environment = os.environ.copy()
    environment.update(
        {
            "SUDHIR_CODEX_ROOT": str(paths.repo_root),
            "SUDHIR_CODEX_STATE": str(paths.state_dir),
            "SUDHIR_CODEX_PI_AGENT_DIR": str(paths.pi_agent_dir),
        }
    )
    try:
        process = subprocess.Popen(
            [
                str(paths.venv_python),
                "-m",
                "sudhir_codex_gateway.management",
                "serve",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            close_fds=True,
            env=environment,
            **detached_process_kwargs(),
        )
    finally:
        os.close(log_fd)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise GatewayError(
                70,
                "gateway_start_failed",
                f"Gateway exited during startup; inspect {paths.gateway_log}",
            )
        status = gateway_status(paths)
        if status["running"] and (is_windows() or status["pid"] == process.pid):
            return int(status["pid"])
        time.sleep(0.1)
    process.terminate()
    raise GatewayError(
        70,
        "gateway_start_timeout",
        (
            "Gateway did not become ready; last health state: "
            f"{json.dumps(status, sort_keys=True)}; inspect {paths.gateway_log}"
        ),
    )


def stop_gateway(paths: RuntimePaths, *, timeout_seconds: float = 10.0) -> bool:
    validate_isolation(paths)
    pid = _read_pid(paths.pid_file)
    if pid is None:
        return False
    if not _process_alive(pid):
        paths.pid_file.unlink(missing_ok=True)
        return False
    if is_windows():
        status = gateway_status(paths)
        if not status["running"] or status["pid"] != pid:
            raise GatewayError(
                73,
                "gateway_process_mismatch",
                "Refusing to stop a PID that failed the private health check",
            )
    else:
        command = _process_command(pid)
        expected_fragment = "sudhir_codex_gateway.management serve"
        if expected_fragment not in command or str(paths.repo_root) not in command:
            raise GatewayError(
                73,
                "gateway_process_mismatch",
                "Refusing to signal a PID that is not this Sudhir-Codex gateway",
            )
    terminate_process(pid)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            paths.pid_file.unlink(missing_ok=True)
            return True
        time.sleep(0.1)
    raise GatewayError(
        70,
        "gateway_stop_timeout",
        "Gateway did not stop before the timeout",
    )


def list_models(paths: RuntimePaths) -> tuple[list[dict[str, Any]], str]:
    start_gateway(paths)
    token = paths.token_file.read_text(encoding="ascii").strip()
    headers = {
        GATEWAY_TOKEN_HEADER: token,
        "User-Agent": "sudhir-codex/0.1",
        **_private_chatgpt_headers(paths),
    }
    response = httpx.get(
        f"{paths.gateway_url}/v1/models",
        params={"client_version": CHATGPT_CLIENT_VERSION},
        headers=headers,
        timeout=10.0,
        follow_redirects=False,
    )
    if response.status_code != 200:
        raise GatewayError(
            69,
            "model_catalog_failed",
            f"Gateway model catalog returned HTTP {response.status_code}",
        )
    document = response.json()
    raw_models = document.get("models") if isinstance(document, dict) else None
    if not isinstance(raw_models, list):
        raise GatewayError(
            69, "model_catalog_invalid", "Gateway returned invalid models"
        )
    models: list[dict[str, Any]] = []
    for item in raw_models:
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
            continue
        slug = item["slug"]
        models.append(
            {
                "id": slug,
                "name": item.get("display_name", slug),
                "description": item.get("description"),
                "kind": "pi" if slug.startswith("pi-") else "gpt",
                "context_window": item.get("context_window"),
                "multi_agent_version": item.get("multi_agent_version"),
            }
        )
    return models, response.headers.get("X-Sudhir-GPT-Catalog", "unknown")


def doctor(paths: RuntimePaths) -> dict[str, Any]:
    ensure_private_state(paths)
    status = gateway_status(paths)
    pi_count = 0
    pi_providers = 0
    try:
        document = json.loads((paths.pi_agent_dir / "models.json").read_text())
        providers = document.get("providers", {})
        if isinstance(providers, dict):
            non_codex = {
                name: value
                for name, value in providers.items()
                if name != "openai-codex" and isinstance(value, dict)
            }
            pi_providers = len(non_codex)
            pi_count = sum(
                len(value.get("models", []))
                for value in non_codex.values()
                if isinstance(value.get("models", []), list)
            )
    except (OSError, ValueError):
        pass
    return {
        "ok": bool(
            paths.repo_root.is_dir()
            and paths.state_dir.resolve() != paths.official_codex_home.resolve()
            and paths.token_file.is_file()
            and paths.config_file.is_file()
        ),
        "platform_scope": platform_scope(),
        "repo_root": str(paths.repo_root.resolve()),
        "state_dir": str(paths.state_dir.resolve()),
        "official_state_dir": str(paths.official_codex_home.resolve()),
        "state_is_independent": (
            paths.state_dir.resolve() != paths.official_codex_home.resolve()
        ),
        "core_binary": str(paths.core_binary),
        "core_binary_present": paths.core_binary.is_file(),
        "installed_launcher": str(paths.installed_launcher),
        "official_codex_binary": shutil.which("codex"),
        "auth_present": paths.private_auth_file.is_file(),
        "auth_mode": file_mode(paths.private_auth_file),
        "token_mode": file_mode(paths.token_file),
        "config_mode": file_mode(paths.config_file),
        "pi_provider_count": pi_providers,
        "pi_model_count": pi_count,
        "gateway": status,
        "telemetry": {
            "analytics": False,
            "feedback": False,
            "otel_logs": "none",
            "otel_traces": "none",
            "otel_metrics": "none",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    paths = runtime_paths_from_env()
    try:
        if args.command == "init":
            ensure_private_state(paths)
            return 0
        if args.command == "serve":
            run_server(
                gateway_settings(paths), port=DEFAULT_PORT, pid_file=paths.pid_file
            )
            return 0
        if args.command == "start":
            print(start_gateway(paths))
            return 0
        if args.command == "stop":
            print("stopped" if stop_gateway(paths) else "not running")
            return 0
        if args.command == "status":
            value = gateway_status(paths)
            print(json.dumps(value, indent=2) if args.json else _status_text(value))
            return 0 if value["running"] else 1
        if args.command == "models":
            models, source = list_models(paths)
            if args.json:
                print(json.dumps({"gpt_catalog": source, "models": models}, indent=2))
            else:
                print(f"GPT catalog: {source}; models: {len(models)}")
                for model in models:
                    print(f"{model['id']}\t{model['name']}")
            return 0
        if args.command == "doctor":
            value = doctor(paths)
            print(json.dumps(value, indent=2) if args.json else _doctor_text(value))
            return 0 if value["ok"] else 1
        if args.command == "auth-import":
            backup = import_official_auth(paths)
            message = "Imported official auth into independent Sudhir-Codex state."
            if backup:
                message += f" Previous private auth backup: {backup}"
            print(message)
            return 0
        if args.command == "mcp-import":
            count = import_official_mcp(paths)
            print(f"Imported {count} MCP server definition(s) into private config.")
            return 0
        if args.command == "rotate-token":
            status = gateway_status(paths)
            if status["running"]:
                raise GatewayError(
                    73,
                    "gateway_must_stop",
                    "Stop the gateway before rotating its client token",
                )
            rotate_gateway_token(paths)
            print("Rotated private gateway token.")
            return 0
        parser.error("unknown command")
    except GatewayError as exc:
        print(f"sudhir-codex: {exc.message}", file=sys.stderr)
        return exc.status if 1 <= exc.status <= 125 else 1
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sudhir-codex-gateway")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "serve", "start", "stop", "auth-import", "mcp-import"):
        subparsers.add_parser(command)
    status = subparsers.add_parser("status")
    status.add_argument("--json", action="store_true")
    models = subparsers.add_parser("models")
    models.add_argument("--json", action="store_true")
    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--json", action="store_true")
    subparsers.add_parser("rotate-token")
    return parser


def _private_chatgpt_headers(paths: RuntimePaths) -> dict[str, str]:
    try:
        document = json.loads(paths.private_auth_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GatewayError(
            77,
            "private_auth_missing",
            "Private auth is unavailable; run `sudhir-codex auth import` or login",
        ) from exc
    if not isinstance(document, dict):
        raise GatewayError(77, "private_auth_invalid", "Private auth is invalid")
    api_key = document.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key:
        return {"Authorization": f"Bearer {api_key}"}
    tokens = document.get("tokens")
    if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str):
        raise GatewayError(
            77, "private_auth_invalid", "Private auth has no access token"
        )
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    account_id = tokens.get("account_id")
    if isinstance(account_id, str) and account_id:
        headers["ChatGPT-Account-Id"] = account_id
    return headers


def _read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
        return value if value > 1 else None
    except (OSError, ValueError):
        return None


def _process_command(pid: int) -> str:
    completed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _status_text(value: dict[str, Any]) -> str:
    state = "running" if value["running"] else "not running"
    return f"Sudhir-Codex gateway: {state}" + (
        f" (pid {value['pid']})" if value.get("pid") else ""
    )


def _doctor_text(value: dict[str, Any]) -> str:
    lines = [
        f"Sudhir-Codex doctor: {'OK' if value['ok'] else 'FAILED'}",
        f"Platform scope: {value['platform_scope']}",
        f"Fork: {value['repo_root']}",
        f"Private state: {value['state_dir']}",
        f"Official state: {value['official_state_dir']}",
        f"Independent: {value['state_is_independent']}",
        f"Core built: {value['core_binary_present']}",
        f"Private auth: {value['auth_present']} ({value['auth_mode']})",
        (
            f"Open-model catalog: {value['pi_model_count']} models across "
            f"{value['pi_provider_count']} providers"
        ),
        f"Gateway running: {value['gateway']['running']}",
        "Optional analytics/feedback/OTEL exporters: disabled",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
