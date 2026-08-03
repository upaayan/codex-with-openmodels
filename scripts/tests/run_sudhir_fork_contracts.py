#!/usr/bin/env python3
"""Run only exact tests and checks selected by the fork-contract register."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACTS = ROOT / "scripts" / "tests" / "sudhir_fork_contracts.toml"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _python_test_command(
    file: str, test_name: str
) -> tuple[list[str], Path, dict[str, str]]:
    environment = os.environ.copy()
    if file.startswith("sudhir_codex/tests/contracts/"):
        module = f"contracts.{Path(file).stem}.{test_name}"
        roots = [ROOT / "sudhir_codex/src", ROOT / "sudhir_codex/tests"]
    elif file.startswith("sudhir_codex/tests/"):
        module = test_name
        roots = [ROOT / "sudhir_codex/src", ROOT / "sudhir_codex/tests"]
    elif file.startswith("scripts/tests/"):
        module = f"{Path(file).stem}.{test_name}"
        roots = [ROOT / "scripts/tests"]
    else:
        raise ValueError(f"unsupported Python test location: {file}")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        [*(str(path) for path in roots), *([existing] if existing else [])]
    )
    return [sys.executable, "-m", "unittest", "-v", module], ROOT, environment


def _rust_crate(file: str) -> str:
    if file.startswith("codex-rs/core/"):
        return "codex-core"
    if file.startswith("codex-rs/arg0/"):
        return "codex-arg0"
    if file.startswith("codex-rs/tui/"):
        return "codex-tui"
    if file.startswith("codex-rs/app-server/"):
        return "codex-app-server"
    raise ValueError(f"unsupported Rust test location: {file}")


def _rust_test_command(
    file: str, test_name: str
) -> tuple[list[str], Path, dict[str, str]]:
    escaped = re.escape(test_name).replace("/", r"\/")
    expression = f"test(/(^|::){escaped}$/)"
    return (
        ["just", "test", "--retries", "0", "-p", _rust_crate(file), "-E", expression],
        ROOT / "codex-rs",
        os.environ.copy(),
    )


def _node_test_command(
    file: str, test_name: str
) -> tuple[list[str], Path, dict[str, str]]:
    escaped = f"^{re.escape(test_name)}$"
    return (
        ["node", "--test", "--test-name-pattern", escaped, Path(file).name],
        ROOT / Path(file).parent,
        os.environ.copy(),
    )


def _test_command(entry: dict[str, Any]) -> tuple[list[str], Path, dict[str, str]]:
    runner = entry["runner"]
    if runner == "python-unittest":
        return _python_test_command(entry["file"], entry["test_name"])
    if runner == "rust-nextest":
        return _rust_test_command(entry["file"], entry["test_name"])
    if runner == "node-test":
        return _node_test_command(entry["file"], entry["test_name"])
    raise ValueError(f"unsupported runner: {runner}")


def _check_command(entry: dict[str, Any]) -> tuple[list[str], Path, dict[str, str]]:
    command = entry.get("derived_command") or entry["command"]
    cwd = ROOT / entry.get("working_directory", "")
    if sys.platform == "win32":
        return (
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd,
            os.environ.copy(),
        )
    return ["bash", "-euo", "pipefail", "-c", command], cwd, os.environ.copy()


def _execute(task: dict[str, Any]) -> dict[str, Any]:
    command, cwd, environment = task["invocation"]
    printable = subprocess.list2cmdline(command)
    print(f"\n[{task['id']}] {printable}", flush=True)
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(
            result.stderr,
            file=sys.stderr,
            end="" if result.stderr.endswith("\n") else "\n",
        )
    return {
        "id": task["id"],
        "ids": task.get("evidence_ids", [task["id"]]),
        "kind": task["kind"],
        "contracts": task["contracts"],
        "command_sha256": _sha256(printable),
        "working_directory": cwd.relative_to(ROOT).as_posix() or ".",
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_sha256": _sha256(result.stdout),
        "stderr_sha256": _sha256(result.stderr),
        "status": "pass" if result.returncode == 0 else "fail",
    }


def _deduplicate(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for task in tasks:
        task_id = task["id"]
        if task_id not in merged:
            merged[task_id] = task
            order.append(task_id)
        else:
            merged[task_id]["contracts"] = sorted(
                set(merged[task_id]["contracts"]) | set(task["contracts"])
            )
    return [merged[task_id] for task_id in order]


def _batch_rust_tests(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    batches: dict[tuple[str, str], dict[str, Any]] = {}
    ordered: list[tuple[str, Any]] = []
    for task in tasks:
        command, cwd, environment = task["invocation"]
        if not command or command[0] != "just":
            task["evidence_ids"] = [task["id"]]
            ordered.append(("task", task))
            continue
        crate = command[command.index("-p") + 1]
        key = (crate, str(cwd))
        if key not in batches:
            batches[key] = {
                "id": f"rust-batch:{crate}",
                "kind": "rust-test-batch",
                "contracts": [],
                "evidence_ids": [],
                "test_names": [],
                "cwd": cwd,
                "environment": environment,
            }
            ordered.append(("batch", key))
        batch = batches[key]
        batch["contracts"].extend(task["contracts"])
        batch["evidence_ids"].append(task["id"])
        batch["test_names"].append(task["test_name"])

    result = []
    for kind, value in ordered:
        if kind == "task":
            result.append(value)
            continue
        batch = batches[value]
        names = list(dict.fromkeys(batch.pop("test_names")))
        expression = (
            "test(/(^|::)(" + "|".join(re.escape(name) for name in names) + ")$/)"
        )
        cwd = batch.pop("cwd")
        environment = batch.pop("environment")
        crate = batch["id"].removeprefix("rust-batch:")
        batch["contracts"] = sorted(set(batch["contracts"]))
        batch["evidence_ids"] = list(dict.fromkeys(batch["evidence_ids"]))
        batch["invocation"] = (
            ["just", "test", "--retries", "0", "-p", crate, "-E", expression],
            cwd,
            environment,
        )
        result.append(batch)
    return result


def _check_compiles_rust(task: dict[str, Any]) -> bool:
    command = task["invocation"][0]
    if not command:
        return False
    shell_text = command[-1] if command[0] in {"bash", "pwsh"} else " ".join(command)
    return bool(
        re.search(
            r"(?m)(?:^|[;&|]\s*)"
            r"(?:cargo\s+(?:build|check|clippy|nextest|run|test)\b|just\s+(?:clippy|fix|test)\b)",
            shell_text,
        )
    )


def build_tasks(
    contracts_path: Path,
    platform: str,
    *,
    phase: str = "all",
) -> list[dict[str, Any]]:
    if phase not in {"all", "prebuild"}:
        raise ValueError(f"unsupported phase: {phase}")
    document = tomllib.loads(contracts_path.read_text(encoding="utf-8"))
    selected = [
        row for row in document.get("contract", []) if platform in row["platforms"]
    ]
    if not selected:
        raise ValueError(f"platform {platform} selected no contract rows")
    test_tasks: list[dict[str, Any]] = []
    check_tasks: list[dict[str, Any]] = []
    for row in selected:
        primary = {
            "file": row["test_file"],
            "test_name": row["test_name"],
            "runner": row["test_runner"],
        }
        test_tasks.append(
            {
                "id": f"{row['id']}:primary",
                "kind": "primary-test",
                "contracts": [row["id"]],
                "runner": primary["runner"],
                "test_name": row["test_name"],
                "invocation": _test_command(primary),
            }
        )
        for supporting in row.get("supporting_tests", []):
            if platform not in supporting["platforms"]:
                continue
            test_tasks.append(
                {
                    "id": supporting.get("inventory_id") or supporting["register_id"],
                    "kind": "supporting-test",
                    "contracts": [row["id"]],
                    "runner": supporting["runner"],
                    "test_name": supporting["test_name"],
                    "invocation": _test_command(supporting),
                }
            )
        for check in row.get("ci_checks", []):
            if platform not in check["platforms"]:
                continue
            check_id = check.get("inventory_id") or (
                f"{check['parent_inventory_id']}:{_sha256(check['derived_command'])[:12]}"
            )
            check_tasks.append(
                {
                    "id": check_id,
                    "kind": "ci-check",
                    "contracts": [row["id"]],
                    "invocation": _check_command(check),
                    "is_setup": (
                        check.get("derived_command") or check["command"]
                    ).startswith("npm ci "),
                }
            )
    check_tasks = _deduplicate(check_tasks)
    prechecks = [task for task in check_tasks if task.get("is_setup")]
    postchecks = [task for task in check_tasks if not task.get("is_setup")]
    checks = [*prechecks, *postchecks]
    for task in checks:
        task["evidence_ids"] = [task["id"]]
    test_tasks = _deduplicate(test_tasks)
    if phase == "prebuild":
        compile_free_checks = [
            task for task in checks if not _check_compiles_rust(task)
        ]
        compile_free_tests = [
            task for task in test_tasks if task["runner"] != "rust-nextest"
        ]
        for task in compile_free_tests:
            task["evidence_ids"] = [task["id"]]
        return [*compile_free_checks, *compile_free_tests]
    return [*prechecks, *_batch_rust_tests(test_tasks), *postchecks]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    parser.add_argument("--platform", choices=("linux", "windows"), required=True)
    parser.add_argument("--phase", choices=("all", "prebuild"), default="all")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()

    try:
        tasks = build_tasks(args.contracts, args.platform, phase=args.phase)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"sudhir-fork-contract-runner: {exc}", file=sys.stderr)
        return 1
    results = []
    for task in tasks:
        result = _execute(task)
        results.append(result)
        if result["status"] != "pass":
            break
    evidence = {
        "schema_version": 1,
        "phase": args.phase,
        "source_commit": os.environ.get("GITHUB_SHA")
        or subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "platform": args.platform,
        "status": "pass"
        if len(results) == len(tasks)
        and all(item["status"] == "pass" for item in results)
        else "fail",
        "selected_task_count": len(tasks),
        "selected_check_count": sum(
            len(task.get("evidence_ids", [task["id"]])) for task in tasks
        ),
        "completed_task_count": len(results),
        "results": results,
    }
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                key: evidence[key]
                for key in (
                    "platform",
                    "status",
                    "selected_task_count",
                    "selected_check_count",
                    "completed_task_count",
                )
            },
            sort_keys=True,
        )
    )
    return 0 if evidence["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
