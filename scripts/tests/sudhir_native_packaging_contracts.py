#!/usr/bin/env python3
"""Fork-owned source contracts for native release packaging."""

from __future__ import annotations

import json
import re
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

import run_sudhir_fork_contracts
import staged_artifact_acceptance
import verify_sudhir_fork_contracts


ROOT = Path(__file__).resolve().parents[2]
SOURCE_WORKFLOW = ROOT / ".github" / "workflows" / "source-ci.yml"
NATIVE_WORKFLOW = ROOT / ".github" / "workflows" / "native-release.yml"
CONTRACTS = ROOT / "scripts" / "tests" / "sudhir_fork_contracts.toml"


def _job_block(text: str, job: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        text,
    )
    if match is None:
        raise AssertionError(f"workflow job is missing: {job}")
    return match.group("body")


class NativePackagingContracts(unittest.TestCase):
    def test_bootstrap_completeness_excludes_register_native_tests(self) -> None:
        inventory = {
            "items": [
                {
                    "inventory_id": "TEST-legacy",
                }
            ]
        }
        contracts = """
[[contract]]
id = "SC-TEST-001"
primary_inventory_ids = ["TEST-legacy"]

[[contract.supporting_tests]]
register_id = "REGTEST-new"
"""
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            inventory_path = temp_path / "inventory.json"
            contracts_path = temp_path / "contracts.toml"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            contracts_path.write_text(contracts, encoding="utf-8")
            try:
                result = verify_sudhir_fork_contracts.bootstrap_completeness(
                    inventory_path,
                    contracts_path,
                )
            except (KeyError, verify_sudhir_fork_contracts.ContractError) as exc:
                self.fail(f"register-native test entered legacy inventory: {exc}")

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["inventory_items"], 1)
        self.assertEqual(result["accounted"], 1)

    def test_prebuild_gate_never_compiles_or_runs_rust_tests(self) -> None:
        forbidden = re.compile(
            r"\b(?:cargo\s+(?:build|test|nextest)|just\s+test|nextest)\b"
        )
        source = SOURCE_WORKFLOW.read_text(encoding="utf-8")
        native = NATIVE_WORKFLOW.read_text(encoding="utf-8")

        for job in ("fork-contracts-linux", "fork-contracts-windows"):
            block = _job_block(source, job)
            self.assertIn("--phase prebuild", block)
            self.assertIsNone(forbidden.search(block), job)

        native_prebuild = _job_block(native, "fork-contracts")
        self.assertIn("--phase prebuild", native_prebuild)
        self.assertIsNone(forbidden.search(native_prebuild))

        for platform in ("linux", "windows"):
            tasks = run_sudhir_fork_contracts.build_tasks(
                CONTRACTS,
                platform,
                phase="prebuild",
            )
            commands = [task["invocation"][0] for task in tasks]
            printable = "\n".join(" ".join(command) for command in commands)
            self.assertIsNone(forbidden.search(printable), platform)
            self.assertTrue(
                all(
                    command[0] != "just" or command[1] == "fmt-check"
                    for command in commands
                )
            )

            kinds = [task["kind"] for task in tasks]
            first_test = next(
                (index for index, kind in enumerate(kinds) if kind != "ci-check"),
                len(kinds),
            )
            self.assertTrue(all(kind == "ci-check" for kind in kinds[:first_test]))

        linux_tasks = run_sudhir_fork_contracts.build_tasks(
            CONTRACTS,
            "linux",
            phase="prebuild",
        )
        self.assertTrue(
            any(
                task["invocation"][0]
                == ["bash", "-euo", "pipefail", "-c", "just fmt-check"]
                for task in linux_tasks
            )
        )

    def test_release_builds_each_native_binary_once(self) -> None:
        workflow = NATIVE_WORKFLOW.read_text(encoding="utf-8")
        macos = _job_block(workflow, "macos")
        ubuntu = _job_block(workflow, "ubuntu")
        windows = _job_block(workflow, "windows-build")

        self.assertEqual(macos.count("cargo build"), 1)
        self.assertEqual(windows.count("cargo build"), 1)
        self.assertNotIn("strategy:", windows)
        self.assertNotIn("matrix.", windows)

        # Linux must finalize bwrap before compiling Codex so Codex can embed
        # the checksum of the exact packaged bytes. The two invocations have
        # disjoint binary sets; no binary is compiled twice.
        self.assertEqual(ubuntu.count("cargo build"), 2)
        for binary in (
            "bwrap",
            "codex",
            "codex-code-mode-host",
            "codex-responses-api-proxy",
        ):
            self.assertEqual(
                len(re.findall(rf"--bin\s+{re.escape(binary)}(?![-\w])", ubuntu)),
                1,
                binary,
            )

        for binary in (
            "codex",
            "codex-app-server",
            "codex-code-mode-host",
            "codex-command-runner",
            "codex-responses-api-proxy",
            "codex-windows-sandbox-setup",
        ):
            self.assertEqual(
                len(re.findall(rf"--bin\s+{re.escape(binary)}(?![-\w])", windows)),
                1,
                binary,
            )

    def test_prebuild_evidence_does_not_claim_uncompiled_rust_contracts(self) -> None:
        commit = "a" * 40
        tasks = run_sudhir_fork_contracts.build_tasks(
            CONTRACTS,
            "linux",
            phase="prebuild",
        )
        evidence = {
            "schema_version": 1,
            "phase": "prebuild",
            "source_commit": commit,
            "platform": "linux",
            "status": "pass",
            "results": [
                {
                    "status": "pass",
                    "ids": task.get("evidence_ids", [task["id"]]),
                }
                for task in tasks
            ],
        }
        rows = {
            row["id"]: row
            for row in tomllib.loads(CONTRACTS.read_text(encoding="utf-8"))["contract"]
        }
        with tempfile.TemporaryDirectory() as temp:
            evidence_path = Path(temp) / "prebuild.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            result = staged_artifact_acceptance._ci_phase(
                SimpleNamespace(
                    test_evidence=evidence_path,
                    platform="linux",
                    source_commit=commit,
                ),
                rows,
            )

        by_id = {item["contract_id"]: item for item in result["results"]}
        self.assertEqual(by_id["SC-STATE-001"]["status"], "pass")
        self.assertEqual(by_id["SC-TUI-001"]["status"], "not-run-compile-free")

    def test_every_native_compile_waits_for_dependency_preflight(self) -> None:
        workflow = NATIVE_WORKFLOW.read_text(encoding="utf-8")
        for job in ("macos", "ubuntu", "windows-build"):
            block = _job_block(workflow, job)
            dependencies = block.split("steps:", 1)[0]
            self.assertIn("fork-contracts", dependencies, job)
            self.assertIn("windows-dependency-preflight", dependencies, job)
            self.assertLess(
                block.index("uses: ./.github/actions/setup-rusty-v8"),
                block.index("cargo build"),
                job,
            )

    def test_public_source_boundaries(self) -> None:
        required = (
            "LICENSE",
            "NOTICE",
            "MODIFICATIONS.md",
            ".github/upstream-base.txt",
            ".github/workflows/source-ci.yml",
            ".github/workflows/native-release.yml",
            ".github/workflows/monthly-upstream-check.yml",
        )
        self.assertTrue(all((ROOT / path).is_file() for path in required))
        workflows = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
        self.assertEqual(
            [path.name for path in workflows],
            [
                "monthly-upstream-check.yml",
                "native-release.yml",
                "source-ci.yml",
            ],
        )
        forbidden = (
            ROOT / "documents" / "BACKEND-MONTHLY-UPGRADE.md",
            ROOT / "documents" / "FRONTEND-UPDATE.md",
            ROOT / "scripts" / "normalize-sudhir-thread-providers.rb",
        )
        self.assertTrue(all(not path.exists() for path in forbidden))
        public_docs = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "README.md",
                ROOT / "MODIFICATIONS.md",
                ROOT / "sudhir_codex" / "README.md",
            )
        )
        self.assertNotRegex(public_docs, r"/Users/|/Applications/Sudhir-Codex\.app")

    def test_archive_layout_and_checksums(self) -> None:
        workflow = NATIVE_WORKFLOW.read_text(encoding="utf-8")
        expected_archives = (
            "codex-with-openmodels-aarch64-apple-darwin.tar.gz",
            "codex-with-openmodels-x86_64-unknown-linux-musl.tar.gz",
            "codex-with-openmodels-x86_64-pc-windows-msvc.zip",
        )
        for archive in expected_archives:
            self.assertEqual(workflow.count(archive) >= 2, True, archive)
        release = _job_block(workflow, "release")
        self.assertIn('sha256sum "${expected[@]}" > SHA256SUMS', release)
        self.assertIn("sha256sum -c SHA256SUMS", release)
        self.assertIn(
            "tar -tzf codex-with-openmodels-aarch64-apple-darwin.tar.gz", release
        )
        self.assertIn(
            "tar -tzf codex-with-openmodels-x86_64-unknown-linux-musl.tar.gz", release
        )
        self.assertIn(
            "unzip -t codex-with-openmodels-x86_64-pc-windows-msvc.zip", release
        )

    def test_extracted_archive_starts_required_commands(self) -> None:
        workflow = NATIVE_WORKFLOW.read_text(encoding="utf-8")
        for job, executable in (
            ("macos", '"$stage/codex"'),
            ("windows", '& (Join-Path $Stage "codex.exe")'),
        ):
            block = _job_block(workflow, job)
            for invocation in (
                "--version",
                "--help",
                "app-server --help",
                "mcp-server --help",
            ):
                self.assertIn(
                    f"{executable} {invocation}", block, f"{job}: {invocation}"
                )

        ubuntu = _job_block(workflow, "ubuntu")
        self.assertIn('"$binary" --version', ubuntu)
        self.assertIn('run_codex_smoke stripped "$stage/codex"', ubuntu)
        for invocation in ("--help", "app-server --help", "mcp-server --help"):
            self.assertIn(
                f'"$stage/codex" {invocation}', ubuntu, f"ubuntu: {invocation}"
            )


if __name__ == "__main__":
    unittest.main()
