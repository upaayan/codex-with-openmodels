"""Runtime controls for the reversible standard-ChatGPT frontend trial."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ._frontend_transition_state import TransitionError
from ._frontend_transition_state import TransitionPaths
from ._frontend_transition_state import _config_semantic_sha256
from ._frontend_transition_state import _load_metadata
from ._frontend_transition_state import _sha256
from ._frontend_transition_state import prepare
from ._frontend_transition_state import primary_config_matches
from ._frontend_transition_state import restore_chrome


def launch(paths: TransitionPaths) -> None:
    _load_metadata(paths)
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
        f"CODEX_ELECTRON_USER_DATA_PATH={paths.profile}",
        "--args",
        f"--user-data-dir={paths.profile}",
    ]
    subprocess.run(command, check=True)


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
        choices=("prepare", "launch", "status", "restore-chrome", "rollback"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = TransitionPaths.from_env()
    try:
        if args.command == "prepare":
            print(json.dumps(prepare(paths), indent=2, sort_keys=True))
        elif args.command == "launch":
            launch(paths)
            print(f"Launched ChatGPT with transition profile {paths.profile}")
        elif args.command == "status":
            print(json.dumps(status(paths), indent=2, sort_keys=True))
        elif args.command == "restore-chrome":
            restore_chrome(paths)
            print(f"Restored Chrome native host {paths.chrome_manifest}")
        elif args.command == "rollback":
            print(f"Transition archived at {rollback(paths)}")
    except (OSError, subprocess.SubprocessError, TransitionError, ValueError) as exc:
        print(f"gpt-pro-frontend-transition: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
