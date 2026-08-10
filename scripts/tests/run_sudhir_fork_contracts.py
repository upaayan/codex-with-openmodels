#!/usr/bin/env python3
"""Run registered non-Rust contract primaries and record Rust as compile-free."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "scripts" / "tests" / "sudhir_fork_contracts.toml"


def _python_target(row: dict[str, Any]) -> str:
    path = Path(row["test_file"])
    if path.parts[:3] == ("sudhir_codex", "tests", "contracts"):
        module = ".".join(path.with_suffix("").parts[2:])
    elif path.parts[:2] == ("sudhir_codex", "tests"):
        module = ".".join(path.with_suffix("").parts[2:])
        if row["test_name"].startswith(f"{module}."):
            return row["test_name"]
    elif path.parts[:2] == ("scripts", "tests"):
        module = ".".join(path.with_suffix("").parts)
    else:
        raise ValueError(f"unsupported Python contract path: {path}")
    return f"{module}.{row['test_name']}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("linux",), required=True)
    parser.add_argument("--phase", choices=("prebuild",), required=True)
    parser.add_argument("--evidence", type=Path)
    arguments = parser.parse_args()

    with REGISTER.open("rb") as handle:
        rows = tomllib.load(handle)["contract"]
    gateway_python = ROOT / "sudhir_codex" / ".venv" / "bin" / "python"
    python = str(gateway_python) if gateway_python.is_file() else sys.executable
    commands: list[list[str]] = []
    rust: list[str] = []
    for row in rows:
        runner = row["test_runner"]
        if runner == "python-unittest":
            commands.append([python, "-m", "unittest", _python_target(row)])
        elif runner == "node-test":
            commands.append(["node", "--test", row["test_file"]])
        elif runner == "rust-nextest":
            rust.append(row["id"])
        else:
            raise ValueError(f"unknown runner: {runner}")

    seen: set[tuple[str, ...]] = set()
    results: list[dict[str, Any]] = []
    environment = os.environ.copy()
    python_paths = [
        str(ROOT / "sudhir_codex" / "src"),
        str(ROOT / "sudhir_codex" / "tests"),
        str(ROOT / "scripts" / "tests"),
        str(ROOT),
    ]
    environment["PYTHONPATH"] = os.pathsep.join(python_paths)
    failed = False
    for command in commands:
        key = tuple(command)
        if key in seen:
            continue
        seen.add(key)
        result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
        results.append({"command": command, "returncode": result.returncode})
        failed = failed or result.returncode != 0

    report = {
        "status": "fail" if failed else "pass",
        "platform": arguments.platform,
        "phase": arguments.phase,
        "executed": results,
        "rust_contracts": rust,
        "rust_runtime": "not-run-compile-free",
    }
    if arguments.evidence:
        arguments.evidence.parent.mkdir(parents=True, exist_ok=True)
        arguments.evidence.write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
