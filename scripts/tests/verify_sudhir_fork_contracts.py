#!/usr/bin/env python3
"""Validate the 33 durable Sudhir-Codex fork contracts without compiling Rust."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACTS = ROOT / "scripts" / "tests" / "sudhir_fork_contracts.toml"
SOURCE_WORKFLOW = ROOT / ".github" / "workflows" / "source-ci.yml"
NATIVE_WORKFLOW = ROOT / ".github" / "workflows" / "native-release.yml"

EXPECTED_IDS = (
    "SC-STATE-001",
    "SC-GATEWAY-001",
    "SC-GATEWAY-002",
    "SC-GATEWAY-003",
    "SC-WEB-SEARCH-001",
    "SC-ARG0-001",
    "SC-CATALOG-001",
    "SC-CATALOG-002",
    "SC-CREDENTIALS-001",
    "SC-REASONING-001",
    "SC-REASONING-002",
    "SC-REASONING-003",
    "SC-REASONING-004",
    "SC-REASONING-005",
    "SC-HISTORY-001",
    "SC-COMPACTION-001",
    "SC-COMPACTION-002",
    "SC-COMPACTION-003",
    "SC-COMPACTION-004",
    "SC-OAUTH-001",
    "SC-OAUTH-WORKER-001",
    "SC-XAI-001",
    "SC-AGENTS-001",
    "SC-AGENTS-002",
    "SC-PI-TOOLS-001",
    "SC-PI-TOOLS-002",
    "SC-PI-TOOLS-003",
    "SC-TUI-001",
    "SC-TUI-002",
    "SC-TUI-003",
    "SC-SOURCE-001",
    "SC-PACKAGING-001",
    "SC-PACKAGING-002",
)
REQUIRED_FIELDS = {
    "id",
    "behavior",
    "patch",
    "source",
    "test_file",
    "test_runner",
    "test_name",
    "acceptance_case",
    "acceptance_mode",
    "acceptance_hosts",
    "lane",
    "platforms",
    "always",
}


class ContractError(RuntimeError):
    pass


def load_contracts(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    if document.get("schema_version") != 1:
        raise ContractError("contract register schema_version must be 1")
    rows = document.get("contract")
    if not isinstance(rows, list):
        raise ContractError("contract register must contain [[contract]] rows")
    return rows


def _assert_test_resolves(row: dict[str, Any]) -> None:
    relative = row["test_file"]
    path = ROOT / relative
    if not path.is_file():
        raise ContractError(f"{row['id']}: test file is missing: {relative}")
    test_name = row["test_name"]
    method = test_name.rsplit(".", 1)[-1]
    source = path.read_text(encoding="utf-8")
    if row["test_runner"] == "node-test":
        if method not in source:
            raise ContractError(f"{row['id']}: Node test is missing: {test_name}")
        return
    occurrences = len(
        re.findall(
            rf"(?m)^\s*(?:async\s+)?fn\s+{re.escape(method)}\s*\(|^\s*def\s+{re.escape(method)}\s*\(",
            source,
        )
    )
    if occurrences != 1:
        raise ContractError(
            f"{row['id']}: {test_name} resolves {occurrences} times in {relative}"
        )
    if row["test_runner"] == "rust-nextest":
        stem = path.stem
        registrations = (
            f"mod {stem};",
            f'#[path = "{path.name}"]',
        )
        candidates = list(path.parent.glob("*.rs")) + list(
            path.parent.parent.glob("*.rs")
        )
        registered = any(
            marker in candidate.read_text(encoding="utf-8")
            for candidate in candidates
            if candidate != path
            for marker in registrations
        )
        if not registered:
            raise ContractError(
                f"{row['id']}: Rust test module is not registered: {relative}"
            )


def lint(path: Path, platform: str | None) -> dict[str, Any]:
    rows = load_contracts(path)
    ids = [row.get("id") for row in rows]
    if tuple(ids) != EXPECTED_IDS:
        raise ContractError(
            "contract register must contain the exact ordered 33-ID set"
        )
    if len(set(ids)) != 33:
        raise ContractError("contract IDs must be unique")

    for row in rows:
        missing = sorted(REQUIRED_FIELDS - row.keys())
        if missing:
            raise ContractError(f"{row['id']}: missing fields: {', '.join(missing)}")
        if row["platforms"] != ["linux"]:
            raise ContractError(f"{row['id']}: only the Linux/WSL platform is allowed")
        if platform is not None and platform not in row["platforms"]:
            raise ContractError(f"{row['id']}: platform {platform} is not registered")
        if row["acceptance_hosts"] not in (["github-linux"], ["staged-macos"]):
            raise ContractError(f"{row['id']}: unsupported acceptance host")
        for source in row["source"]:
            if not (ROOT / source).is_file():
                raise ContractError(f"{row['id']}: source file is missing: {source}")
        _assert_test_resolves(row)

    source_workflow = SOURCE_WORKFLOW.read_text(encoding="utf-8")
    native_workflow = NATIVE_WORKFLOW.read_text(encoding="utf-8")
    required_commands = (
        "python scripts/tests/verify_sudhir_fork_contracts.py lint --platform linux",
        "python scripts/tests/run_sudhir_fork_contracts.py --platform linux --phase prebuild",
    )
    for command in required_commands:
        if source_workflow.count(command) != 1:
            raise ContractError(
                f"source CI must contain exactly one command: {command}"
            )
    forbidden = {
        source_workflow: ("\n  gateway-windows:", "runs-on: windows-"),
        native_workflow: ("runs-on: windows-", "pc-windows-msvc", "windows-release-"),
    }
    for workflow, tokens in forbidden.items():
        for token in tokens:
            if token in workflow:
                raise ContractError(f"native Windows workflow surface remains: {token}")
    if (ROOT / "scripts" / "sudhir-codex.ps1").exists():
        raise ContractError(
            "native Windows installer remains: scripts/sudhir-codex.ps1"
        )

    return {
        "status": "pass",
        "contract_count": 33,
        "contracts": ids,
        "platform": platform,
        "rust_runtime": "not-run-compile-free",
    }


def audit(path: Path, baseline: str) -> dict[str, Any]:
    current = {row["id"]: row for row in load_contracts(path)}
    result = subprocess.run(
        ["git", "show", f"{baseline}:scripts/tests/sudhir_fork_contracts.toml"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ContractError(result.stderr.strip())
    previous = {
        row["id"]: row
        for row in tomllib.loads(result.stdout).get("contract", [])
        if row.get("id") in EXPECTED_IDS
    }
    removed = sorted(set(previous) - set(current))
    if removed:
        raise ContractError(f"previous contracts disappeared: {', '.join(removed)}")
    report = lint(path, None)
    report["baseline"] = baseline
    report["all_previous_fork_contracts_survived"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    commands = parser.add_subparsers(dest="command", required=True)
    lint_parser = commands.add_parser("lint")
    lint_parser.add_argument("--platform", choices=("linux",))
    audit_parser = commands.add_parser("audit")
    audit_parser.add_argument("--baseline", required=True)
    arguments = parser.parse_args()
    try:
        report = (
            lint(arguments.contracts, arguments.platform)
            if arguments.command == "lint"
            else audit(arguments.contracts, arguments.baseline)
        )
    except (ContractError, OSError, tomllib.TOMLDecodeError) as error:
        print(f"fork-contract verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
