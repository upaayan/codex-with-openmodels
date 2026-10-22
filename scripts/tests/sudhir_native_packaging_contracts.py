#!/usr/bin/env python3
"""Compile-free contracts for the macOS and Linux release surface."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NATIVE_WORKFLOW = ROOT / ".github" / "workflows" / "native-release.yml"


def _job_block(text: str, job: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        text,
    )
    if match is None:
        raise AssertionError(f"workflow job is missing: {job}")
    return match.group("body")


def assert_agent_acceptance_scheduled(workflow: str) -> None:
    """SC-AGENTS-001 must run on each native artifact before it can be uploaded."""
    gate = "python3 scripts/tests/verify_sudhir_fork_contracts.py lint --platform linux"
    if gate not in _job_block(workflow, "tag-check"):
        raise AssertionError(
            "native tag-check must enforce the pre-build contract gate"
        )
    for job, target in (
        ("macos", "aarch64-apple-darwin"),
        ("ubuntu", "x86_64-unknown-linux-musl"),
    ):
        block = _job_block(workflow, job)
        command = (
            'python3 "${GITHUB_WORKSPACE}/scripts/tests/sudhir_agents_artifact.py" '
            '"${RUNNER_TEMP}/' + ("openmodels-macos" if job == "macos" else "openmodels-linux") + '/codex"'
        )
        steps = re.split(r"(?m)^      - ", block)
        matches = [step for step in steps if command in step]
        if len(matches) != 1 or command + "\n" not in matches[0]:
            raise AssertionError(
                f"{job}: full SC-AGENTS-001 artifact matrix is not scheduled"
            )
        if re.search(r"(?m)^\s+(if|continue-on-error):", matches[0]):
            raise AssertionError(
                f"{job}: agent artifact acceptance must be unconditional and blocking"
            )
        if "needs: tag-check" not in block:
            raise AssertionError(
                f"{job}: native compilation must depend on the pre-build gate"
            )
        if (
            not block.index("cargo build")
            < block.index(command)
            < block.index("actions/upload-artifact@")
        ):
            raise AssertionError(
                f"{job}: agent acceptance must follow compilation and precede upload"
            )


class NativePackagingContracts(unittest.TestCase):
    def test_agent_acceptance_is_required_before_native_release(self) -> None:
        workflow = NATIVE_WORKFLOW.read_text(encoding="utf-8")
        assert_agent_acceptance_scheduled(workflow)
        for job, target in (("macos", "aarch64-apple-darwin"), ("ubuntu", "x86_64-unknown-linux-musl")):
            command = (
                'python3 "${GITHUB_WORKSPACE}/scripts/tests/sudhir_agents_artifact.py" '
                '"${RUNNER_TEMP}/' + ("openmodels-macos" if job == "macos" else "openmodels-linux") + '/codex"'
            )
            for replacement in ("true", command + " --single"):
                with self.subTest(target=target, replacement=replacement):
                    with self.assertRaises(AssertionError):
                        assert_agent_acceptance_scheduled(
                            workflow.replace(command, replacement)
                        )

    def test_public_source_boundaries(self) -> None:
        required = (
            "LICENSE",
            "NOTICE",
            "MODIFICATIONS.md",
            ".github/upstream-base.txt",
            ".github/workflows/source-ci.yml",
            ".github/workflows/rust-focused.yml",
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
                "rust-focused.yml",
                "source-ci.yml",
            ],
        )
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        forbidden = {
            "documents/BACKEND-MONTHLY-UPGRADE.md",
            "documents/plan-audit-implementation/sudhir_codex_plan.md",
            "documents/plan-audit-implementation/sudhir_codex_implementation.md",
            "documents/plan-audit-implementation/sudhir_codex_audit.md",
            "scripts/normalize-sudhir-thread-providers.rb",
            "scripts/sudhir-codex.ps1",
        }
        self.assertTrue(forbidden.isdisjoint(tracked))
        public_docs = "\n".join(
            (ROOT / path).read_text(encoding="utf-8")
            for path in ("README.md", "MODIFICATIONS.md", "sudhir_codex/README.md")
        )
        self.assertNotRegex(public_docs, r"/Users/|/Applications/Sudhir-Codex\.app")

    def test_archive_layout_and_checksums(self) -> None:
        workflow = NATIVE_WORKFLOW.read_text(encoding="utf-8")
        archives = (
            "codex-with-openmodels-aarch64-apple-darwin.tar.gz",
            "codex-with-openmodels-x86_64-unknown-linux-musl.tar.gz",
        )
        for archive in archives:
            self.assertGreaterEqual(workflow.count(archive), 2, archive)
        self.assertNotIn("  push:", workflow)
        for job, platform, tool in (("macos", "macos", "shasum -a 256"), ("ubuntu", "linux", "sha256sum")):
            block = _job_block(workflow, job)
            self.assertIn(f"if: inputs.platforms == '{platform}'", block)
            self.assertIn(" > SHA256SUMS", block)
            self.assertIn(f"{tool} -c SHA256SUMS", block)
            self.assertIn("retention-days: 30", block)
        self.assertNotIn("pc-windows-msvc", workflow)

    def test_extracted_archive_starts_required_commands(self) -> None:
        workflow = NATIVE_WORKFLOW.read_text(encoding="utf-8")
        macos = _job_block(workflow, "macos")
        ubuntu = _job_block(workflow, "ubuntu")
        for invocation in (
            "--version",
            "--help",
            "app-server --help",
            "mcp-server --help",
        ):
            self.assertIn(f'"$stage/codex" {invocation}', macos)
        self.assertIn('"$binary" --version', ubuntu)
        for invocation in ("--help", "app-server --help", "mcp-server --help"):
            self.assertIn(f'"$stage/codex" {invocation}', ubuntu)
        self.assertNotRegex(workflow, r"(?m)^\s+runs-on:\s*windows-")


if __name__ == "__main__":
    unittest.main()
