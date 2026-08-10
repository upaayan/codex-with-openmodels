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


class NativePackagingContracts(unittest.TestCase):
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
        release = _job_block(workflow, "release")
        self.assertIn('sha256sum "${expected[@]}" > SHA256SUMS', release)
        self.assertIn("sha256sum -c SHA256SUMS", release)
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
