#!/usr/bin/env python3
"""Verify and query the cumulative Sudhir-Codex fork-contract register."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACTS = ROOT / "scripts" / "tests" / "sudhir_fork_contracts.toml"
SOURCE_WORKFLOW = ROOT / ".github" / "workflows" / "source-ci.yml"
NATIVE_WORKFLOW = ROOT / ".github" / "workflows" / "native-release.yml"
ACCEPTANCE_HARNESS = ROOT / "scripts" / "tests" / "staged_artifact_acceptance.py"

BASELINE_IDS = (
    "SC-STATE-001",
    "SC-GATEWAY-001",
    "SC-GATEWAY-002",
    "SC-WEB-SEARCH-001",
    "SC-ARG0-001",
    "SC-CATALOG-001",
    "SC-CATALOG-002",
    "SC-CREDENTIALS-001",
    "SC-REASONING-001",
    "SC-REASONING-002",
    "SC-REASONING-003",
    "SC-REASONING-004",
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
    "SC-TUI-001",
    "SC-TUI-002",
    "SC-TUI-003",
    "SC-SOURCE-001",
    "SC-PACKAGING-001",
    "SC-PACKAGING-002",
    "SC-WINDOWS-001",
    "SC-WINDOWS-002",
)

GATEWAY_IDS = {
    "SC-STATE-001",
    "SC-GATEWAY-001",
    "SC-WEB-SEARCH-001",
    "SC-CATALOG-001",
    "SC-CATALOG-002",
    "SC-CREDENTIALS-001",
    "SC-REASONING-001",
    "SC-REASONING-002",
    "SC-REASONING-003",
    "SC-REASONING-004",
    "SC-HISTORY-001",
    "SC-COMPACTION-004",
    "SC-OAUTH-001",
    "SC-XAI-001",
    "SC-AGENTS-002",
    "SC-PI-TOOLS-002",
}
CORE_IDS = {
    "SC-GATEWAY-002",
    "SC-ARG0-001",
    "SC-COMPACTION-001",
    "SC-COMPACTION-002",
    "SC-COMPACTION-003",
    "SC-AGENTS-001",
    "SC-PI-TOOLS-001",
}
TUI_IDS = {"SC-TUI-001", "SC-TUI-002", "SC-TUI-003"}
PACKAGING_IDS = {
    "SC-SOURCE-001",
    "SC-PACKAGING-001",
    "SC-PACKAGING-002",
    "SC-WINDOWS-001",
    "SC-WINDOWS-002",
}

EXPECTED_MATRIX: dict[str, dict[str, Any]] = {}
for contract_id in GATEWAY_IDS:
    EXPECTED_MATRIX[contract_id] = {
        "test_runner": "python-unittest",
        "lane": "gateway",
        "platforms": ["linux", "windows"],
        "acceptance_mode": "ci",
        "acceptance_hosts": ["github-linux", "github-windows"],
        "always": False,
    }
for contract_id in CORE_IDS:
    EXPECTED_MATRIX[contract_id] = {
        "test_runner": "rust-nextest",
        "lane": "core",
        "platforms": ["linux"],
        "acceptance_mode": "artifact",
        "acceptance_hosts": ["staged-macos"],
        "always": contract_id == "SC-AGENTS-001",
    }
EXPECTED_MATRIX["SC-OAUTH-WORKER-001"] = {
    "test_runner": "node-test",
    "lane": "node",
    "platforms": ["linux", "windows"],
    "acceptance_mode": "ci",
    "acceptance_hosts": ["github-linux", "github-windows"],
    "always": False,
}
for contract_id in TUI_IDS:
    EXPECTED_MATRIX[contract_id] = {
        "test_runner": "rust-nextest",
        "lane": "tui-app-server",
        "platforms": ["linux"],
        "acceptance_mode": "ci",
        "acceptance_hosts": ["github-linux"],
        "always": False,
    }
EXPECTED_MATRIX["SC-SOURCE-001"] = {
    "test_runner": "python-unittest",
    "lane": "packaging",
    "platforms": ["linux"],
    "acceptance_mode": "source",
    "acceptance_hosts": ["github-linux"],
    "always": False,
}
for contract_id in ("SC-PACKAGING-001", "SC-PACKAGING-002"):
    EXPECTED_MATRIX[contract_id] = {
        "test_runner": "python-unittest",
        "lane": "packaging",
        "platforms": ["linux", "windows"],
        "acceptance_mode": "artifact",
        "acceptance_hosts": ["staged-macos"],
        "always": False,
    }
for contract_id in ("SC-WINDOWS-001", "SC-WINDOWS-002"):
    EXPECTED_MATRIX[contract_id] = {
        "test_runner": "python-unittest",
        "lane": "packaging",
        "platforms": ["windows"],
        "acceptance_mode": "ci",
        "acceptance_hosts": ["github-windows"],
        "always": False,
    }

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
VALID_RUNNERS = {"python-unittest", "rust-nextest", "node-test"}
VALID_LANES = {"gateway", "core", "tui-app-server", "node", "packaging"}
VALID_PLATFORMS = {"linux", "windows"}
VALID_MODES = {"artifact", "source", "ci"}
VALID_HOSTS = {"github-linux", "github-windows", "staged-macos"}
EXPECTED_LANES = {
    "linux": {"gateway", "core", "tui-app-server", "node", "packaging"},
    "windows": {"gateway", "node", "packaging"},
}
POST_ACTIVATION_CASES = {
    "deepseek-opencode-replay",
    "same-model-cross-provider",
    "selected-model-pressure",
    "history-exec-model-switch",
    "sol-parent-luna-child",
}
RUNTIME_ROOTS = (
    "sudhir_codex/src/sudhir_codex_gateway/",
    "sudhir_codex/cursor_worker/worker.mjs",
    "sudhir_codex/cursor_worker/pi-auth-worker.mjs",
    "sudhir_codex/cursor_worker/package.json",
    "sudhir_codex/cursor_worker/package-lock.json",
    "sudhir_codex/pyproject.toml",
)


class ContractError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return f"{prefix}-{_sha256(payload)[:16]}"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _run(
    *args: str, cwd: Path = ROOT, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise ContractError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr.strip()}"
        )
    return result


def _load_contract_document(path: Path) -> dict[str, Any]:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ContractError(f"cannot read contract register {path}: {exc}") from exc
    contracts = document.get("contract")
    if not isinstance(contracts, list):
        raise ContractError("contract register must contain [[contract]] rows")
    return document


def _contracts_by_id(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = document["contract"]
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    duplicates = sorted(item for item, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ContractError(f"duplicate contract IDs: {', '.join(duplicates)}")
    if any(not isinstance(item, str) or not item for item in ids):
        raise ContractError("every contract requires a non-empty string id")
    return {row["id"]: row for row in rows}


def _test_occurrences(path: Path, runner: str, test_name: str) -> int:
    text = path.read_text(encoding="utf-8")
    method = test_name.rsplit(".", 1)[-1]
    if runner == "python-unittest":
        return len(re.findall(rf"(?m)^\s*def\s+{re.escape(method)}\s*\(", text))
    if runner == "rust-nextest":
        return len(
            re.findall(
                rf"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+{re.escape(method)}\s*\(",
                text,
            )
        )
    if runner == "node-test":
        return text.count(test_name)
    raise ContractError(f"unsupported test runner: {runner}")


def _assert_registered_file(row_id: str, field: str, relative: str) -> Path:
    path = ROOT / relative
    if not path.is_file():
        raise ContractError(f"{row_id}: {field} does not exist as a file: {relative}")
    return path


def _module_registration_count(test_file: str) -> int:
    path = Path(test_file)
    stem = path.stem
    candidates: list[Path]
    if test_file.startswith("codex-rs/core/tests/suite/"):
        candidates = [ROOT / "codex-rs/core/tests/suite/mod.rs"]
    elif test_file.startswith("codex-rs/app-server/tests/suite/v2/"):
        candidates = [ROOT / "codex-rs/app-server/tests/suite/v2/mod.rs"]
    elif test_file.startswith("codex-rs/tui/src/chatwidget/tests/"):
        candidates = [ROOT / "codex-rs/tui/src/chatwidget/tests.rs"]
    elif test_file == "codex-rs/core/src/sudhir_launcher_contracts_tests.rs":
        candidates = [ROOT / "codex-rs/core/src/shell_snapshot.rs"]
    elif test_file == "codex-rs/arg0/src/sudhir_arg0_contracts_tests.rs":
        candidates = [ROOT / "codex-rs/arg0/src/lib.rs"]
    else:
        return 1
    declaration = re.compile(
        rf"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+{re.escape(stem)}\s*;"
    )
    return sum(
        len(declaration.findall(path.read_text(encoding="utf-8")))
        for path in candidates
    )


def _workflow_job_block(text: str, job: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        text,
    )
    if match is None:
        raise ContractError(f"workflow job is missing: {job}")
    return match.group("body")


def _verify_workflows() -> None:
    source = SOURCE_WORKFLOW.read_text(encoding="utf-8")
    for job, display, platform in (
        ("fork-contracts-linux", "Fork contracts (Linux)", "linux"),
        ("fork-contracts-windows", "Fork contracts (Windows)", "windows"),
    ):
        block = _workflow_job_block(source, job)
        if f"name: {display}" not in block:
            raise ContractError(f"{job}: exact display name is missing")
        verifier = f"verify_sudhir_fork_contracts.py lint --platform {platform}"
        runner = f"run_sudhir_fork_contracts.py --platform {platform}"
        if (
            verifier not in block
            or runner not in block
            or "--phase prebuild" not in block
        ):
            raise ContractError(f"{job}: exact verifier/runner commands are missing")

    native = NATIVE_WORKFLOW.read_text(encoding="utf-8")
    fork = _workflow_job_block(native, "fork-contracts")
    normalized_fork = " ".join(fork.replace("\\\n", " ").split())
    for required in (
        "needs: tag-check",
        "platform: linux",
        "os: ubuntu-22.04",
        "platform: windows",
        "os: windows-2022",
        "verify_sudhir_fork_contracts.py lint --platform",
        "run_sudhir_fork_contracts.py --platform",
        "--phase prebuild",
        "staged_artifact_acceptance.py --phase ci",
    ):
        if required not in fork and required not in normalized_fork:
            raise ContractError(f"native fork-contracts job is missing: {required}")

    dependency_requirements = {
        "windows-dependency-preflight": ("fork-contracts",),
        "macos": ("tag-check", "fork-contracts", "windows-dependency-preflight"),
        "ubuntu": ("tag-check", "fork-contracts", "windows-dependency-preflight"),
        "windows-build": (
            "tag-check",
            "fork-contracts",
            "windows-dependency-preflight",
        ),
        "windows": ("tag-check", "fork-contracts", "windows-build"),
        "release": ("tag-check", "fork-contracts", "macos", "ubuntu", "windows"),
    }
    for job, dependencies in dependency_requirements.items():
        block = _workflow_job_block(native, job)
        prefix = block.split("steps:", 1)[0]
        for dependency in dependencies:
            if dependency not in prefix:
                raise ContractError(f"native {job} does not depend on {dependency}")
        if re.search(r"(?m)^\s*if:\s*always\(\)", prefix):
            raise ContractError(
                f"native {job} bypasses failed dependencies with if: always()"
            )


def _verify_acceptance(rows: dict[str, dict[str, Any]]) -> None:
    if not ACCEPTANCE_HARNESS.is_file():
        raise ContractError(
            f"acceptance harness is missing: {ACCEPTANCE_HARNESS.relative_to(ROOT)}"
        )
    tree = ast.parse(
        ACCEPTANCE_HARNESS.read_text(encoding="utf-8"), ACCEPTANCE_HARNESS.as_posix()
    )
    values: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {
                    "CASE_IMPLEMENTATIONS",
                    "POST_ACTIVATION_CASES",
                    "ROLLBACK_CASES",
                    "DEFERRED_WINDOWS_JOBS",
                }:
                    value_node = node.value
                    if value_node is not None:
                        values[target.id] = ast.literal_eval(value_node)
    implementations = values.get("CASE_IMPLEMENTATIONS")
    if not isinstance(implementations, dict):
        raise ContractError(
            "acceptance harness must define literal CASE_IMPLEMENTATIONS"
        )
    cases = [row["acceptance_case"] for row in rows.values()]
    duplicates = sorted(case for case, count in Counter(cases).items() if count > 1)
    if duplicates:
        raise ContractError(f"acceptance cases are not unique: {', '.join(duplicates)}")
    if set(implementations) != set(cases):
        raise ContractError(
            "acceptance implementation table differs from the sole register"
        )
    for row in rows.values():
        case = row["acceptance_case"]
        hosts = implementations.get(case)
        if not isinstance(hosts, list) or sorted(hosts) != sorted(
            row["acceptance_hosts"]
        ):
            raise ContractError(
                f"{row['id']}: acceptance hosts are not implemented exactly"
            )
    if set(values.get("POST_ACTIVATION_CASES", ())) != POST_ACTIVATION_CASES:
        raise ContractError("acceptance harness post-activation case set is incomplete")
    if set(values.get("ROLLBACK_CASES", ())) != {"sol-parent-luna-child"}:
        raise ContractError("acceptance harness rollback case set is incomplete")
    if values.get("DEFERRED_WINDOWS_JOBS") != {
        "SC-WINDOWS-001": "Windows Python and rusty_v8 dependency preflight",
        "SC-WINDOWS-002": "Windows x64 bundle",
    }:
        raise ContractError("acceptance harness Windows deferred-job mapping changed")

    harness_text = ACCEPTANCE_HARNESS.read_text(encoding="utf-8")
    for required in (
        'parser.add_argument("--operational-root", type=Path)',
        "str(args.operational_root)",
        '"decrypt_signatures": decrypt_signatures',
        "_inter_agent_encrypted_count(requests)",
    ):
        if required not in harness_text:
            raise ContractError(
                f"acceptance harness is missing required rollback/live proof: {required}"
            )


def _verify_patch_stack(rows: dict[str, dict[str, Any]]) -> None:
    upstream_file = ROOT / ".github" / "upstream-base.txt"
    if not upstream_file.is_file():
        raise ContractError(".github/upstream-base.txt is missing")
    upstream = upstream_file.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", upstream):
        raise ContractError(
            ".github/upstream-base.txt must contain one full commit hash"
        )
    result = _run("git", "log", "--format=%s", f"{upstream}..HEAD")
    subjects = result.stdout.splitlines()
    for label in sorted({row["patch"] for row in rows.values()}):
        matching = [subject for subject in subjects if subject.startswith(f"{label}:")]
        if len(matching) != 1:
            raise ContractError(
                f"patch label {label} resolves to {len(matching)} commit subjects"
            )
    for contract_id in rows:
        matching = [
            subject
            for subject in subjects
            if f"[{contract_id}" in subject
            or f" {contract_id}]" in subject
            or f" {contract_id} " in subject
        ]
        if len(matching) != 1:
            raise ContractError(
                f"{contract_id}: commit subject occurrence count is {len(matching)}"
            )


def lint(contracts_path: Path, platform: str | None) -> dict[str, Any]:
    document = _load_contract_document(contracts_path)
    rows = _contracts_by_id(document)
    missing_baseline = sorted(set(BASELINE_IDS) - rows.keys())
    if missing_baseline:
        raise ContractError(
            f"baseline contracts disappeared: {', '.join(missing_baseline)}"
        )
    if set(EXPECTED_MATRIX) != set(BASELINE_IDS):
        raise ContractError("internal bootstrap matrix does not cover all baseline IDs")

    for row_id, row in rows.items():
        missing = sorted(REQUIRED_FIELDS - row.keys())
        if missing:
            raise ContractError(f"{row_id}: missing fields: {', '.join(missing)}")
        if row["test_runner"] not in VALID_RUNNERS:
            raise ContractError(f"{row_id}: invalid test_runner")
        if row["lane"] not in VALID_LANES:
            raise ContractError(f"{row_id}: invalid lane")
        if row["acceptance_mode"] not in VALID_MODES:
            raise ContractError(f"{row_id}: invalid acceptance_mode")
        if not isinstance(row["always"], bool):
            raise ContractError(f"{row_id}: always must be boolean")
        if not isinstance(row["source"], list) or not row["source"]:
            raise ContractError(f"{row_id}: source must be a non-empty list")
        platforms = row["platforms"]
        if (
            not isinstance(platforms, list)
            or not platforms
            or set(platforms) - VALID_PLATFORMS
        ):
            raise ContractError(f"{row_id}: invalid platforms")
        if len(platforms) != len(set(platforms)):
            raise ContractError(f"{row_id}: duplicate platforms")
        hosts = row["acceptance_hosts"]
        if not isinstance(hosts, list) or not hosts or set(hosts) - VALID_HOSTS:
            raise ContractError(f"{row_id}: invalid acceptance_hosts")
        if len(hosts) != len(set(hosts)):
            raise ContractError(f"{row_id}: duplicate acceptance_hosts")
        if row_id == "SC-AGENTS-001" and row["always"] is not True:
            raise ContractError("SC-AGENTS-001 must remain always=true")
        if row_id in EXPECTED_MATRIX:
            for field, expected in EXPECTED_MATRIX[row_id].items():
                actual = row[field]
                if isinstance(expected, list):
                    if sorted(actual) != sorted(expected):
                        raise ContractError(f"{row_id}: bootstrap {field} changed")
                elif actual != expected:
                    raise ContractError(f"{row_id}: bootstrap {field} changed")

        for source in row["source"]:
            _assert_registered_file(row_id, "source", source)
        test_path = _assert_registered_file(row_id, "test_file", row["test_file"])
        occurrences = _test_occurrences(test_path, row["test_runner"], row["test_name"])
        if occurrences != 1:
            raise ContractError(f"{row_id}: primary test occurs {occurrences} times")
        registrations = _module_registration_count(row["test_file"])
        if registrations != 1:
            raise ContractError(
                f"{row_id}: test module registration occurs {registrations} times"
            )

        for supporting in row.get("supporting_tests", []):
            for field in ("file", "test_name", "runner", "platforms"):
                if field not in supporting:
                    raise ContractError(f"{row_id}: supporting test misses {field}")
            identities = [
                field
                for field in ("inventory_id", "register_id")
                if supporting.get(field)
            ]
            if len(identities) != 1:
                raise ContractError(
                    f"{row_id}: supporting test requires exactly one inventory_id or register_id"
                )
            support_path = _assert_registered_file(
                row_id, "supporting test file", supporting["file"]
            )
            if (
                _test_occurrences(
                    support_path, supporting["runner"], supporting["test_name"]
                )
                != 1
            ):
                raise ContractError(
                    f"{row_id}: supporting test does not resolve exactly once"
                )
            if set(supporting["platforms"]) - set(row["platforms"]):
                raise ContractError(
                    f"{row_id}: supporting test selects an undeclared platform"
                )

        for check in row.get("ci_checks", []):
            for field in ("workflow", "job", "step", "command", "platforms"):
                if field not in check:
                    raise ContractError(f"{row_id}: ci check misses {field}")
            if not check.get("inventory_id") and not check.get("parent_inventory_id"):
                raise ContractError(f"{row_id}: ci check lacks inventory identity")
            if set(check["platforms"]) - set(row["platforms"]):
                raise ContractError(
                    f"{row_id}: ci check selects an undeclared platform"
                )

    if platform is not None:
        selected = [row for row in rows.values() if platform in row["platforms"]]
        if not selected:
            raise ContractError(f"platform {platform} selects no contracts")
        lanes = {row["lane"] for row in selected}
        if lanes != EXPECTED_LANES[platform]:
            raise ContractError(
                f"platform {platform} lane set is {sorted(lanes)}, expected {sorted(EXPECTED_LANES[platform])}"
            )

    _verify_workflows()
    _verify_acceptance(rows)
    _verify_patch_stack(rows)
    return {
        "status": "pass",
        "contracts": len(rows),
        "platform": platform,
        "selected": len([row for row in rows.values() if platform in row["platforms"]])
        if platform
        else len(rows),
    }


def _parse_workflow_steps(text: str) -> list[dict[str, str]]:
    jobs_text = text.split("\njobs:\n", 1)
    if len(jobs_text) != 2:
        raise ContractError("source workflow has no jobs block")
    lines = jobs_text[1].splitlines()
    steps: list[dict[str, str]] = []
    job = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        job_match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if job_match:
            job = job_match.group(1)
        name_match = re.match(r"^      - name:\s*(.+?)\s*$", line)
        if name_match and job:
            step = {
                "job": job,
                "step": name_match.group(1),
                "working_directory": "",
                "shell": "",
                "run": "",
            }
            cursor = index + 1
            while cursor < len(lines):
                candidate = lines[cursor]
                if re.match(r"^      - (?:name:|uses:)", candidate) or re.match(
                    r"^  [A-Za-z0-9_-]+:\s*$", candidate
                ):
                    break
                field = re.match(
                    r"^        (working-directory|shell):\s*(.+?)\s*$", candidate
                )
                if field:
                    step[field.group(1).replace("-", "_")] = field.group(2)
                run = re.match(r"^        run:\s*(.*)$", candidate)
                if run:
                    if run.group(1) and run.group(1) not in {"|", ">"}:
                        step["run"] = run.group(1).strip()
                    else:
                        block: list[str] = []
                        cursor += 1
                        while cursor < len(lines):
                            content = lines[cursor]
                            if (
                                content.strip()
                                and len(content) - len(content.lstrip()) < 10
                            ):
                                cursor -= 1
                                break
                            block.append(content[10:] if len(content) >= 10 else "")
                            cursor += 1
                        step["run"] = "\n".join(block).rstrip()
                cursor += 1
            if step["run"]:
                steps.append(step)
            index = cursor - 1
        index += 1
    return steps


def _platform_for_job(job: str) -> str:
    return "windows" if "windows" in job else "linux"


def _workflow_inventory_item(
    step: dict[str, str], command: str, *, kind: str = "ci-check"
) -> dict[str, Any]:
    platform = _platform_for_job(step["job"])
    inventory_id = _stable_id(
        "WF",
        "source-ci.yml",
        step["job"],
        step["step"],
        platform,
        command,
    )
    return {
        "inventory_id": inventory_id,
        "kind": kind,
        "workflow": ".github/workflows/source-ci.yml",
        "job": step["job"],
        "step": step["step"],
        "command": command,
        "working_directory": step["working_directory"],
        "shell": step["shell"],
        "platforms": [platform],
    }


def _constant_string_tuple(path: Path, name: str) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), path.as_posix())
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == name for target in targets
            ):
                value = ast.literal_eval(node.value)
                if not isinstance(value, tuple) or not all(
                    isinstance(item, str) for item in value
                ):
                    raise ContractError(f"{path}: {name} is not a literal string tuple")
                return value
    raise ContractError(f"{path}: {name} is missing")


def _find_rust_test_file(test_name: str) -> str:
    pattern = re.compile(
        rf"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+{re.escape(test_name)}\s*\("
    )
    matches = []
    for path in (ROOT / "codex-rs").rglob("*.rs"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if pattern.search(text):
            matches.append(path.relative_to(ROOT).as_posix())
    if len(matches) != 1:
        raise ContractError(
            f"Rust test {test_name} resolves in {len(matches)} files: {matches}"
        )
    return matches[0]


def inventory_existing(
    source_workflow: Path, legacy_manifest: Path, output: Path
) -> None:
    workflow_text = source_workflow.read_text(encoding="utf-8")
    steps = _parse_workflow_steps(workflow_text)
    items: list[dict[str, Any]] = []
    wrapper_ids: dict[str, list[str]] = {"gateway": [], "rust": []}

    for step in steps:
        run = step["run"].strip()
        if step["step"] in {
            "Check licence, files, and workflow surface",
            "Smoke-test Windows installation and gateway lifecycle",
        }:
            items.append(_workflow_inventory_item(step, run))
            continue
        if step["step"] == "Check the pinned Windows Node workers":
            marker = "$WorkerSmokeRoot ="
            simple, _, smoke = run.partition(marker)
            for line in simple.splitlines():
                command = line.strip()
                if command.startswith(
                    ("npm ci ", "node --check ", "node --test ", "npm ls ")
                ):
                    kind = (
                        "primary-test"
                        if command == "node --test pi-auth-worker.test.mjs"
                        else "ci-check"
                    )
                    items.append(_workflow_inventory_item(step, command, kind=kind))
            if smoke:
                items.append(_workflow_inventory_item(step, marker + smoke))
            continue
        for line in run.splitlines():
            command = line.strip()
            if not command or command.startswith(
                ("set ", "sudo apt-get", "sudo DEBIAN", "--no-install", "pkg-config")
            ):
                continue
            wrapper = re.fullmatch(
                r"python3? scripts/tests/sudhir_targeted_regressions\.py (gateway|rust)",
                command,
            )
            if wrapper:
                platform = _platform_for_job(step["job"])
                wrapper_ids[wrapper.group(1)].append(
                    _stable_id("WRAPPER", step["job"], step["step"], platform, command)
                )
                continue
            if command.startswith("just test -p ") and not command.endswith(
                " reasoning"
            ):
                parts = command.split()
                test_name = parts[-1]
                item = _workflow_inventory_item(step, command, kind="supporting-test")
                item.update(
                    {
                        "file": _find_rust_test_file(test_name),
                        "test_name": test_name,
                        "runner": "rust-nextest",
                    }
                )
                items.append(item)
                continue
            if command.startswith(
                (
                    "python -m compileall ",
                    "ruff check ",
                    "bash scripts/tests/",
                    "npm ci ",
                    "node --check ",
                    "node --test ",
                    "npm ls ",
                    ".\\scripts\\tests\\",
                    "just fmt-check",
                    "just test -p codex-tui reasoning",
                )
            ):
                kind = (
                    "primary-test"
                    if command == "node --test pi-auth-worker.test.mjs"
                    else "ci-check"
                )
                items.append(_workflow_inventory_item(step, command, kind=kind))

    gateway_tests = _constant_string_tuple(legacy_manifest, "GATEWAY_TESTS")
    rust_tests = _constant_string_tuple(legacy_manifest, "RUST_TESTS")
    for test_name in gateway_tests:
        module = test_name.split(".", 1)[0]
        file = f"sudhir_codex/tests/{module}.py"
        item = {
            "inventory_id": _stable_id("TEST", "python-unittest", file, test_name),
            "kind": "supporting-test",
            "file": file,
            "test_name": test_name,
            "runner": "python-unittest",
            "platforms": ["linux", "windows"],
            "expanded_by": sorted(wrapper_ids["gateway"]),
        }
        if _test_occurrences(ROOT / file, "python-unittest", test_name) != 1:
            raise ContractError(
                f"legacy Python test does not resolve once: {test_name}"
            )
        items.append(item)
    for test_name in rust_tests:
        file = _find_rust_test_file(test_name)
        items.append(
            {
                "inventory_id": _stable_id("TEST", "rust-nextest", file, test_name),
                "kind": "supporting-test",
                "file": file,
                "test_name": test_name,
                "runner": "rust-nextest",
                "platforms": ["linux"],
                "expanded_by": sorted(wrapper_ids["rust"]),
            }
        )

    counts = Counter(item["inventory_id"] for item in items)
    duplicates = sorted(item for item, count in counts.items() if count > 1)
    if duplicates:
        raise ContractError(f"inventory stable IDs collided: {duplicates}")
    _write_json(
        output,
        {
            "schema_version": 1,
            "source_workflow_sha256": _sha256(source_workflow.read_bytes()),
            "legacy_manifest_sha256": _sha256(legacy_manifest.read_bytes()),
            "items": sorted(items, key=lambda item: item["inventory_id"]),
        },
    )


def bootstrap_completeness(
    inventory_path: Path, contracts_path: Path
) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    items = {item["inventory_id"]: item for item in inventory.get("items", [])}
    document = _load_contract_document(contracts_path)
    rows = _contracts_by_id(document)
    accounted: Counter[str] = Counter()
    derived: dict[str, list[dict[str, Any]]] = {}
    for row in rows.values():
        for inventory_id in row.get("primary_inventory_ids", []):
            accounted[inventory_id] += 1
        for supporting in row.get("supporting_tests", []):
            if inventory_id := supporting.get("inventory_id"):
                accounted[inventory_id] += 1
        for check in row.get("ci_checks", []):
            if check.get("inventory_id"):
                accounted[check["inventory_id"]] += 1
            if check.get("parent_inventory_id"):
                derived.setdefault(check["parent_inventory_id"], []).append(check)
    for parent, children in derived.items():
        if parent not in items:
            raise ContractError(
                f"derived check parent is absent from inventory: {parent}"
            )
        original = items[parent]["command"]
        if not original.startswith("npm ls "):
            raise ContractError(f"derived check parent is not npm ls: {parent}")
        expected_packages = {
            part for part in original.split()[3:] if part.startswith("@")
        }
        derived_packages: list[str] = []
        for child in children:
            command = child.get("derived_command", "")
            if not command.startswith("npm ls "):
                raise ContractError(f"derived npm check has invalid command: {command}")
            derived_packages.extend(
                part for part in command.split()[3:] if part.startswith("@")
            )
        if set(derived_packages) != expected_packages or len(derived_packages) != len(
            set(derived_packages)
        ):
            raise ContractError(
                f"derived npm checks do not partition parent packages: {parent}"
            )
        accounted[parent] += 1

    unknown = sorted(set(accounted) - set(items))
    duplicates = sorted(item for item, count in accounted.items() if count != 1)
    unaccounted = sorted(set(items) - set(accounted))
    result = {
        "status": "pass"
        if not unknown and not duplicates and not unaccounted
        else "fail",
        "inventory_items": len(items),
        "accounted": len(accounted),
        "unknown": unknown,
        "duplicate_or_invalid_count": duplicates,
        "unaccounted": unaccounted,
    }
    if result["status"] != "pass":
        raise ContractError(json.dumps(result, indent=2, sort_keys=True))
    return result


def _git_show(commit: str, path: str) -> bytes | None:
    binary = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if binary.returncode != 0:
        missing = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}:{path}"],
            cwd=ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if missing.returncode != 0:
            return None
        raise ContractError(binary.stderr.decode("utf-8", errors="replace"))
    return binary.stdout


def _git_text(commit: str, path: str) -> str | None:
    data = _git_show(commit, path)
    return None if data is None else data.decode("utf-8")


def affected(contracts_path: Path, baseline: str, candidate: str, output: Path) -> None:
    candidate_doc = _load_contract_document(contracts_path)
    candidate_rows = _contracts_by_id(candidate_doc)
    old_text = _git_text(baseline, "scripts/tests/sudhir_fork_contracts.toml")
    old_rows: dict[str, dict[str, Any]] = {}
    if old_text is not None:
        old_rows = _contracts_by_id(tomllib.loads(old_text))
    changed = set(
        _run(
            "git", "diff", "--name-only", f"{baseline}..{candidate}"
        ).stdout.splitlines()
    )
    selected = []
    for contract_id, row in candidate_rows.items():
        paths = set(row["source"]) | {row["test_file"]}
        is_affected = (
            bool(paths & changed) or old_rows.get(contract_id) != row or row["always"]
        )
        if is_affected:
            selected.append(
                {
                    "id": contract_id,
                    "acceptance_case": row["acceptance_case"],
                    "acceptance_mode": row["acceptance_mode"],
                    "acceptance_hosts": row["acceptance_hosts"],
                    "always": row["always"],
                }
            )
    _write_json(
        output,
        {
            "schema_version": 1,
            "baseline": baseline,
            "candidate": candidate,
            "contracts": selected,
        },
    )


def audit(
    contracts_path: Path,
    baseline: str,
    candidate: str,
    old_upstream: str,
    new_upstream: str,
    range_diff: Path,
    output: Path,
) -> None:
    if not range_diff.is_file() or not range_diff.read_text(encoding="utf-8").strip():
        raise ContractError("range-diff evidence is missing or empty")
    candidate_rows = _contracts_by_id(_load_contract_document(contracts_path))
    old_text = _git_text(baseline, "scripts/tests/sudhir_fork_contracts.toml")
    if old_text is None:
        raise ContractError("baseline has no fork-contract register")
    old_rows = _contracts_by_id(tomllib.loads(old_text))
    reports = []
    failures = []
    for contract_id, old in old_rows.items():
        current = candidate_rows.get(contract_id)
        report = {
            "contract_id": contract_id,
            "previous_behavior_survived": current is not None
            and current.get("behavior") == old.get("behavior"),
            "dedicated_test_survived": current is not None
            and current.get("test_file") == old.get("test_file")
            and current.get("test_name") == old.get("test_name"),
            "patch_source_mapping_valid": current is not None
            and current.get("patch") == old.get("patch")
            and current.get("source") == old.get("source"),
            "acceptance_case_survived": current is not None
            and current.get("acceptance_case") == old.get("acceptance_case"),
        }
        if not all(value for key, value in report.items() if key != "contract_id"):
            failures.append(contract_id)
        reports.append(report)
    result = {
        "schema_version": 1,
        "baseline": baseline,
        "candidate": candidate,
        "old_upstream": old_upstream,
        "new_upstream": new_upstream,
        "range_diff_sha256": _sha256(range_diff.read_bytes()),
        "contracts": reports,
        "all_previous_fork_contracts_survived": not failures,
        "failures": failures,
    }
    _write_json(output, result)
    if failures:
        raise ContractError(f"previous contracts failed audit: {', '.join(failures)}")


def _runtime_paths(commit: str) -> set[str]:
    paths = set(_run("git", "ls-tree", "-r", "--name-only", commit).stdout.splitlines())
    selected = set()
    for path in paths:
        if any(
            path == root or (root.endswith("/") and path.startswith(root))
            for root in RUNTIME_ROOTS
        ):
            selected.add(path)
    return selected


def _file_bytes(path: Path) -> bytes | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ContractError(f"runtime path is not a regular non-symlink file: {path}")
    return path.read_bytes()


def _hash_or_absent(data: bytes | None) -> str:
    return "absent" if data is None else _sha256(data)


def _make_patch(
    preimages: dict[str, bytes | None], postimages: dict[str, bytes | None]
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="sudhir-contract-patch-") as temp:
        repo = Path(temp)
        _run("git", "init", "-q", cwd=repo)
        _run("git", "config", "user.name", "Sudhir Contract Tool", cwd=repo)
        _run("git", "config", "user.email", "contracts@invalid", cwd=repo)
        for path, content in preimages.items():
            if content is not None:
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        _run("git", "add", "--all", cwd=repo)
        _run("git", "commit", "-q", "-m", "preimage", "--allow-empty", cwd=repo)
        for path, content in postimages.items():
            target = repo / path
            if content is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        result = subprocess.run(
            ["git", "diff", "--binary", "--full-index", "HEAD", "--"],
            cwd=repo,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise ContractError(result.stderr.decode("utf-8", errors="replace"))
        return result.stdout


def gateway_deploy_plan(
    contracts_path: Path,
    baseline: str,
    candidate: str,
    candidate_root: Path,
    operational_root: Path,
    output_dir: Path,
    owner_decisions: Path | None,
) -> int:
    rows = _contracts_by_id(_load_contract_document(contracts_path))
    parity = sorted(_runtime_paths(baseline) | _runtime_paths(candidate))
    for row in rows.values():
        if row["lane"] in {"gateway", "node"}:
            for source in row["source"]:
                if source not in parity:
                    raise ContractError(
                        f"registered runtime source is outside parity set: {source}"
                    )
    output_dir.mkdir(parents=True, exist_ok=True)
    conflicts = []
    entries = []
    preimages: dict[str, bytes | None] = {}
    candidate_images: dict[str, bytes | None] = {}
    baseline_images: dict[str, bytes | None] = {}
    for path in parity:
        base = _git_show(baseline, path)
        selected = _git_show(candidate, path)
        checkout = _file_bytes(candidate_root / path)
        if checkout != selected:
            raise ContractError(f"candidate checkout differs from commit at {path}")
        operational = _file_bytes(operational_root / path)
        base_hash = _hash_or_absent(base)
        candidate_hash = _hash_or_absent(selected)
        operational_hash = _hash_or_absent(operational)
        if operational != selected and operational != base:
            conflicts.append(
                {
                    "path": path,
                    "baseline_sha256_or_absent": base_hash,
                    "candidate_sha256_or_absent": candidate_hash,
                    "operational_sha256_or_absent": operational_hash,
                }
            )
        operation = (
            "delete"
            if selected is None
            else (
                "add"
                if base is None
                else ("modify" if base != selected else "unchanged")
            )
        )
        entries.append(
            {
                "path": path,
                "operation": operation,
                "baseline_sha256_or_absent": base_hash,
                "candidate_sha256_or_absent": candidate_hash,
                "preimage_sha256_or_absent": operational_hash,
            }
        )
        preimages[path] = operational
        candidate_images[path] = selected
        baseline_images[path] = base

    _write_json(output_dir / "gateway-conflicts.json", conflicts)
    decisions: list[dict[str, Any]] = []
    if conflicts:
        if owner_decisions is None:
            return 78
        decision_doc = json.loads(owner_decisions.read_text(encoding="utf-8"))
        if decision_doc.get("candidate_commit") != candidate:
            raise ContractError("owner decisions candidate commit mismatch")
        decisions = decision_doc.get("decisions", [])
        by_path = {decision.get("path"): decision for decision in decisions}
        if set(by_path) != {conflict["path"] for conflict in conflicts}:
            raise ContractError(
                "owner decisions do not match the complete conflict set"
            )
        for conflict in conflicts:
            decision = by_path[conflict["path"]]
            if (
                decision.get("operational_sha256_or_absent")
                != conflict["operational_sha256_or_absent"]
            ):
                raise ContractError(
                    f"owner decision preimage changed: {conflict['path']}"
                )
            if decision.get("decision") != "replace-with-candidate":
                raise ContractError(
                    f"owner did not approve candidate parity: {conflict['path']}"
                )
            if not decision.get("owner_approval_reference"):
                raise ContractError(
                    f"owner approval reference is missing: {conflict['path']}"
                )
    elif owner_decisions is not None:
        decision_doc = json.loads(owner_decisions.read_text(encoding="utf-8"))
        if decision_doc.get("decisions"):
            raise ContractError(
                "owner decisions contain paths when there are no conflicts"
            )

    normalized_decisions = sorted(decisions, key=lambda item: item["path"])
    _write_json(output_dir / "gateway-reconciliation.json", normalized_decisions)
    candidate_patch = _make_patch(baseline_images, candidate_images)
    forward_patch = _make_patch(preimages, candidate_images)
    reverse_patch = _make_patch(candidate_images, preimages)
    (output_dir / "gateway-candidate-forward.patch").write_bytes(candidate_patch)
    (output_dir / "gateway-operational-forward.patch").write_bytes(forward_patch)
    (output_dir / "gateway-operational-reverse.patch").write_bytes(reverse_patch)
    changed_operational = [
        path for path in parity if preimages[path] != candidate_images[path]
    ]
    manifest = {
        "schema_version": 1,
        "baseline_commit": baseline,
        "candidate_commit": candidate,
        "entries": entries,
        "conflicts_sha256": _sha256(
            (output_dir / "gateway-conflicts.json").read_bytes()
        ),
        "reconciliation_sha256": _sha256(
            (output_dir / "gateway-reconciliation.json").read_bytes()
        ),
        "deployment_required": bool(changed_operational),
        "node_refresh_required": any(
            path
            in {
                "sudhir_codex/cursor_worker/package.json",
                "sudhir_codex/cursor_worker/package-lock.json",
            }
            for path in changed_operational
        ),
        "python_refresh_required": "sudhir_codex/pyproject.toml" in changed_operational,
    }
    _write_json(output_dir / "gateway-deploy-manifest.json", manifest)
    return 0


def gateway_deploy_verify(
    manifest_path: Path, operational_root: Path, expect: str
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    field = (
        "preimage_sha256_or_absent"
        if expect == "preimage"
        else "candidate_sha256_or_absent"
    )
    failures = []
    for entry in manifest.get("entries", []):
        actual = _hash_or_absent(_file_bytes(operational_root / entry["path"]))
        if actual != entry[field]:
            failures.append(
                {"path": entry["path"], "expected": entry[field], "actual": actual}
            )
    if failures:
        raise ContractError(json.dumps(failures, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint_parser = subparsers.add_parser("lint")
    lint_parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    lint_parser.add_argument("--platform", choices=sorted(VALID_PLATFORMS))

    inventory_parser = subparsers.add_parser("inventory-existing")
    inventory_parser.add_argument("--source-workflow", type=Path, required=True)
    inventory_parser.add_argument("--legacy-manifest", type=Path, required=True)
    inventory_parser.add_argument("--output", type=Path, required=True)

    completeness_parser = subparsers.add_parser("bootstrap-completeness")
    completeness_parser.add_argument("--inventory", type=Path, required=True)
    completeness_parser.add_argument(
        "--contracts", type=Path, default=DEFAULT_CONTRACTS
    )

    affected_parser = subparsers.add_parser("affected")
    affected_parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    affected_parser.add_argument("--baseline", required=True)
    affected_parser.add_argument("--candidate", required=True)
    affected_parser.add_argument("--output", type=Path, required=True)

    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    audit_parser.add_argument("--baseline", required=True)
    audit_parser.add_argument("--candidate", required=True)
    audit_parser.add_argument("--old-upstream", required=True)
    audit_parser.add_argument("--new-upstream", required=True)
    audit_parser.add_argument("--range-diff", type=Path, required=True)
    audit_parser.add_argument("--output", type=Path, required=True)

    deploy_parser = subparsers.add_parser("gateway-deploy-plan")
    deploy_parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    deploy_parser.add_argument("--baseline", required=True)
    deploy_parser.add_argument("--candidate", required=True)
    deploy_parser.add_argument("--candidate-root", type=Path, required=True)
    deploy_parser.add_argument("--operational-root", type=Path, required=True)
    deploy_parser.add_argument("--owner-decisions", type=Path)
    deploy_parser.add_argument("--output-dir", type=Path, required=True)

    verify_parser = subparsers.add_parser("gateway-deploy-verify")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--operational-root", type=Path, required=True)
    verify_parser.add_argument(
        "--expect", choices=("preimage", "candidate"), required=True
    )

    args = parser.parse_args()
    try:
        if args.command == "lint":
            print(json.dumps(lint(args.contracts, args.platform), sort_keys=True))
        elif args.command == "inventory-existing":
            inventory_existing(args.source_workflow, args.legacy_manifest, args.output)
        elif args.command == "bootstrap-completeness":
            print(
                json.dumps(
                    bootstrap_completeness(args.inventory, args.contracts),
                    sort_keys=True,
                )
            )
        elif args.command == "affected":
            affected(args.contracts, args.baseline, args.candidate, args.output)
        elif args.command == "audit":
            audit(
                args.contracts,
                args.baseline,
                args.candidate,
                args.old_upstream,
                args.new_upstream,
                args.range_diff,
                args.output,
            )
        elif args.command == "gateway-deploy-plan":
            return gateway_deploy_plan(
                args.contracts,
                args.baseline,
                args.candidate,
                args.candidate_root,
                args.operational_root,
                args.output_dir,
                args.owner_decisions,
            )
        elif args.command == "gateway-deploy-verify":
            gateway_deploy_verify(args.manifest, args.operational_root, args.expect)
        return 0
    except (ContractError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"sudhir-fork-contracts: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
