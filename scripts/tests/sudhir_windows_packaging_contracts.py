#!/usr/bin/env python3
"""Fork-owned source contracts for the Windows native release lane."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NATIVE_WORKFLOW = ROOT / ".github" / "workflows" / "native-release.yml"
SETUP_V8_ACTION = ROOT / ".github" / "actions" / "setup-rusty-v8" / "action.yml"


def _job_block(text: str, job: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        text,
    )
    if match is None:
        raise AssertionError(f"workflow job is missing: {job}")
    return match.group("body")


class WindowsPackagingContracts(unittest.TestCase):
    def test_python_and_v8_preflight_precedes_compile(self) -> None:
        workflow = NATIVE_WORKFLOW.read_text(encoding="utf-8")
        preflight = _job_block(workflow, "windows-dependency-preflight")
        self.assertIn("actions/setup-python@", preflight)
        self.assertIn("uses: ./.github/actions/setup-rusty-v8", preflight)
        self.assertIn("target: x86_64-pc-windows-msvc", preflight)
        self.assertNotIn("cargo build", preflight)

        build = _job_block(workflow, "windows-build")
        self.assertIn("windows-dependency-preflight", build)
        self.assertNotIn("strategy:", build)
        self.assertNotIn("matrix.", build)
        self.assertEqual(build.count("cargo build"), 1)
        self.assertLess(
            build.index("actions/setup-python@"), build.index("cargo build")
        )
        self.assertLess(
            build.index("uses: ./.github/actions/setup-rusty-v8"),
            build.index("cargo build"),
        )

        action = SETUP_V8_ACTION.read_text(encoding="utf-8")
        for required in (
            "resolved-v8-crate-version",
            "*-pc-windows-msvc",
            "tr -d '\\r'",
            "Expected exactly two checksums",
            "sha256sum -c",
            "RUSTY_V8_ARCHIVE",
            "RUSTY_V8_SRC_BINDING_PATH",
        ):
            self.assertIn(required, action)

    def test_native_gateway_lifecycle(self) -> None:
        workflow = NATIVE_WORKFLOW.read_text(encoding="utf-8")
        windows = _job_block(workflow, "windows")
        for required in (
            "Smoke-test Windows installation and gateway lifecycle",
            "sudhir_codex_gateway.management start",
            "sudhir_codex_gateway.management status --json",
            "sudhir_codex_gateway.management stop",
            "Gateway PID file remained after stop",
        ):
            self.assertIn(required, windows)


if __name__ == "__main__":
    unittest.main()
