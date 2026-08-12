"""Runtime controls for the reversible standard-ChatGPT frontend trial."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ._frontend_transition_control import ControlRuntimeError
from ._frontend_transition_control import apply_control_config
from ._frontend_transition_control import provision_control_runtime
from ._frontend_transition_control import runtime_status

from ._frontend_transition_state import TransitionError
from ._frontend_transition_state import TransitionPaths
from ._frontend_transition_state import _config_semantic_sha256
from ._frontend_transition_state import _load_metadata
from ._frontend_transition_state import _sha256
from ._frontend_transition_state import _write_atomic
from ._frontend_transition_state import _verify_official_resources
from ._frontend_transition_state import prepare
from ._frontend_transition_state import primary_config_matches
from ._frontend_transition_state import restore_chrome


def sync_control_runtime(paths: TransitionPaths) -> dict[str, Any]:
    """Refresh official app metadata and the isolated Computer Use runtime."""

    metadata = _load_metadata(paths)
    _identifier, version, build, browser_hash = _verify_official_resources(paths)
    control_metadata = provision_control_runtime(
        paths.control_runtime,
        paths.state,
    )
    source = paths.config.read_text(encoding="utf-8")
    updated = apply_control_config(
        source,
        paths.control_runtime,
        official_version=version,
        browser_client_hash=browser_hash,
    )
    if updated != source:
        _write_atomic(paths.config, updated.encode("utf-8"), mode=0o600)
    metadata.update(control_metadata)
    metadata.update(
        {
            "officialVersion": version,
            "officialBuild": build,
            "browserClientSha256": browser_hash,
            "legacyCuaSource": str(paths.legacy_cua_source),
            "controlSyncedAt": dt.datetime.now(dt.UTC).isoformat(),
        }
    )
    _write_atomic(
        paths.metadata,
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        mode=0o600,
    )
    return control_metadata


def launch(paths: TransitionPaths) -> None:
    sync_control_runtime(paths)
    metadata = _load_metadata(paths)
    control = paths.control_runtime
    if not paths.wrapper.is_file() or not os.access(paths.wrapper, os.X_OK):
        raise TransitionError(f"Transition launcher is missing: {paths.wrapper}")
    if not paths.profile.is_dir():
        raise TransitionError(
            f"Transition Electron profile is missing: {paths.profile}"
        )
    command = [
        "/usr/bin/open",
        "-n",
        "-j",
        "-g",
        "-a",
        str(paths.official_app),
        "--env",
        f"CODEX_CLI_PATH={paths.wrapper}",
        "--env",
        f"CODEX_HOME={paths.state}",
        "--env",
        f"SUDHIR_CODEX_ROOT={paths.repo_root}",
        "--env",
        f"SUDHIR_CODEX_STATE={paths.state}",
        "--env",
        f"SUDHIR_CODEX_GATEWAY_STATE={paths.primary_state}",
        "--env",
        f"SUDHIR_CODEX_PI_AGENT_DIR={paths.pi_agent_dir}",
        "--env",
        "CODEX_APP_SERVER_FORCE_CLI=1",
        "--env",
        f"CODEX_NODE_REPL_PATH={control.node_repl}",
        "--env",
        f"CODEX_BROWSER_USE_NODE_PATH={control.node}",
        "--env",
        "SKY_CUA_SERVICE_PATH=",
        "--env",
        "SKY_CUA_SERVICE_NATIVE_PIPE_PATH=",
        "--env",
        "SUDHIR_CUA=0",
        "--env",
        f"SUDHIR_BROWSER_CLIENT_SHA256S={metadata['browserClientSha256']}",
        "--env",
        f"CODEX_ELECTRON_USER_DATA_PATH={paths.profile}",
        "--args",
        f"--user-data-dir={paths.profile}",
    ]
    subprocess.run(command, check=True)
    if _wait_for_app_server(paths, timeout_seconds=20):
        # The app may install a newer bundled Computer Use plugin during startup.
        time.sleep(1.5)
        sync_control_runtime(paths)


def _process_rows() -> list[tuple[int, int, str]]:
    completed = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[tuple[int, int, str]] = []
    for line in completed.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows


def transition_processes(paths: TransitionPaths) -> list[tuple[int, int, str]]:
    marker = f"--user-data-dir={paths.profile}"
    return [row for row in _process_rows() if marker in row[2]]


def _chatgpt_main_processes(
    paths: TransitionPaths,
) -> list[tuple[int, int, str]]:
    executable = str(paths.official_app / "Contents" / "MacOS" / "ChatGPT")
    return [
        row
        for row in _process_rows()
        if row[2] == executable or row[2].startswith(f"{executable} ")
    ]


def _transition_main_processes(
    paths: TransitionPaths,
) -> list[tuple[int, int, str]]:
    marker = f"--user-data-dir={paths.profile}"
    return [row for row in _chatgpt_main_processes(paths) if marker in row[2]]


def _official_main_processes(
    paths: TransitionPaths,
) -> list[tuple[int, int, str]]:
    marker = f"--user-data-dir={paths.profile}"
    return [row for row in _chatgpt_main_processes(paths) if marker not in row[2]]


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_processes(
    processes: list[tuple[int, int, str]],
    *,
    timeout_seconds: float = 10,
) -> None:
    pids = sorted({pid for pid, _ppid, _command in processes})
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and any(_process_exists(pid) for pid in pids):
        time.sleep(0.25)

    remaining = [pid for pid in pids if _process_exists(pid)]
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    kill_deadline = time.monotonic() + 3
    while time.monotonic() < kill_deadline and any(
        _process_exists(pid) for pid in remaining
    ):
        time.sleep(0.1)
    still_running = [pid for pid in remaining if _process_exists(pid)]
    if still_running:
        raise TransitionError(
            f"Could not stop conflicting ChatGPT processes: {still_running}"
        )


def _healthy_status(value: dict[str, Any]) -> bool:
    control = value.get("controlRuntime")
    return (
        value.get("running") is True
        and value.get("appServerRunning") is True
        and isinstance(control, dict)
        and control.get("ready") is True
    )


def _activate_chatgpt(paths: TransitionPaths) -> None:
    subprocess.run(
        ["/usr/bin/open", "-a", str(paths.official_app)],
        check=True,
    )


def ensure(paths: TransitionPaths) -> dict[str, Any]:
    """Guarantee that the visible ChatGPT instance uses the Sudhir backend."""

    official = _official_main_processes(paths)
    if official:
        _terminate_processes(official)

    current = status(paths)
    if _healthy_status(current):
        _activate_chatgpt(paths)
        return {
            **current,
            "launcherAction": "activated",
            "primaryConfigDriftDetected": current.get("primaryConfigUnchanged")
            is not True,
        }

    transition = _transition_main_processes(paths)
    if transition:
        _terminate_processes(transition)

    launch(paths)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        current = status(paths)
        if _healthy_status(current):
            _activate_chatgpt(paths)
            return {
                **current,
                "launcherAction": "launched",
                "primaryConfigDriftDetected": current.get("primaryConfigUnchanged")
                is not True,
            }
        time.sleep(0.5)
    raise TransitionError("ChatGPT started without a healthy Sudhir-Codex app-server")


def _wait_for_app_server(
    paths: TransitionPaths,
    *,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    main_executable = str(paths.official_app / "Contents" / "MacOS" / "ChatGPT")
    while time.monotonic() < deadline:
        transition = transition_processes(paths)
        main_pids = {
            pid for pid, _ppid, command in transition if main_executable in command
        }
        if main_pids and any(
            ppid in main_pids
            and "sudhir-codex-core" in command
            and "app-server" in command
            for _pid, ppid, command in _process_rows()
        ):
            return True
        time.sleep(0.25)
    return False


def status(paths: TransitionPaths) -> dict[str, Any]:
    metadata = _load_metadata(paths)
    primary_config = paths.primary_state / "config.toml"
    primary_hash = _sha256(primary_config) if primary_config.is_file() else None
    transition_hash = _sha256(paths.config) if paths.config.is_file() else None
    baseline_hash = (
        _sha256(paths.baseline_config) if paths.baseline_config.is_file() else None
    )
    chrome_hash = (
        _sha256(paths.chrome_manifest) if paths.chrome_manifest.is_file() else None
    )
    processes = transition_processes(paths)
    main_executable = str(paths.official_app / "Contents" / "MacOS" / "ChatGPT")
    main_pids = {pid for pid, _ppid, command in processes if main_executable in command}
    all_rows = _process_rows()
    app_server_running = any(
        ppid in main_pids and "sudhir-codex-core" in command and "app-server" in command
        for _pid, ppid, command in all_rows
    )
    return {
        "prepared": True,
        "running": bool(main_pids),
        "appServerRunning": app_server_running,
        "primaryConfigUnchanged": primary_config_matches(paths, metadata),
        "primaryConfigSha256": primary_hash,
        "primaryConfigSemanticSha256": _config_semantic_sha256(primary_config),
        "transitionConfigSha256": transition_hash,
        "baselineConfigSha256": baseline_hash,
        "chromeManifestSha256": chrome_hash,
        "chromeManifestMatchesSnapshot": chrome_hash
        == metadata.get("chromeManifestSha256"),
        "transitionState": str(paths.state),
        "transitionProfile": str(paths.profile),
        "controlRuntime": runtime_status(paths.control_runtime),
    }


def rollback(paths: TransitionPaths) -> Path:
    if transition_processes(paths):
        raise TransitionError(
            "Quit only the transition ChatGPT instance before rollback"
        )
    metadata = _load_metadata(paths)
    if not primary_config_matches(paths, metadata):
        raise TransitionError(
            "Primary config no longer matches the recorded transition baseline"
        )

    restore_chrome(paths)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = paths.rollback_root / f"gpt-pro-frontend-transition-{stamp}"
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    archived_state = destination / paths.state.name
    archived_profile = destination / paths.profile.name
    try:
        os.replace(paths.state, archived_state)
        if paths.profile.exists():
            os.replace(paths.profile, archived_profile)
    except Exception:
        if archived_profile.exists() and not paths.profile.exists():
            os.replace(archived_profile, paths.profile)
        if archived_state.exists() and not paths.state.exists():
            os.replace(archived_state, paths.state)
        destination.rmdir()
        raise
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare, launch, inspect, or roll back the standard ChatGPT frontend trial"
    )
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "sync-control-runtime",
            "launch",
            "ensure",
            "status",
            "restore-chrome",
            "rollback",
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = TransitionPaths.from_env()
    try:
        if args.command == "prepare":
            print(json.dumps(prepare(paths), indent=2, sort_keys=True))
        elif args.command == "sync-control-runtime":
            print(json.dumps(sync_control_runtime(paths), indent=2, sort_keys=True))
        elif args.command == "launch":
            launch(paths)
            print(f"Launched ChatGPT with transition profile {paths.profile}")
        elif args.command == "ensure":
            print(json.dumps(ensure(paths), indent=2, sort_keys=True))
        elif args.command == "status":
            print(json.dumps(status(paths), indent=2, sort_keys=True))
        elif args.command == "restore-chrome":
            restore_chrome(paths)
            print(f"Restored Chrome native host {paths.chrome_manifest}")
        elif args.command == "rollback":
            print(f"Transition archived at {rollback(paths)}")
    except (
        ControlRuntimeError,
        OSError,
        subprocess.SubprocessError,
        TransitionError,
        ValueError,
    ) as exc:
        print(f"gpt-pro-frontend-transition: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
