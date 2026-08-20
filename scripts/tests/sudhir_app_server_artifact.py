#!/usr/bin/env python3
"""Verify model-picker persistence against an already-built Codex artifact."""

import argparse
import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
from pathlib import Path


TIMEOUT_SECONDS = 30


def send_message(process: subprocess.Popen[str], message: dict[str, object]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()


def read_response(
    process: subprocess.Popen[str], request_id: int
) -> dict[str, object]:
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        while time.monotonic() < deadline:
            events = selector.select(deadline - time.monotonic())
            if not events:
                break
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"app-server exited before response {request_id}: {process.poll()}"
                )
            message = json.loads(line)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(
                        f"app-server returned an error for {request_id}: {message['error']}"
                    )
                return message
    finally:
        selector.close()
    raise TimeoutError(f"timed out waiting for app-server response {request_id}")


def verify(binary: Path) -> None:
    binary = binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="sudhir-app-server-artifact-") as home:
        config_path = Path(home) / "config.toml"
        config_path.write_text(
            'model = "gpt-default"\n'
            'model_reasoning_effort = "high"\n'
            "check_for_update_on_startup = false\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["CODEX_HOME"] = home
        with tempfile.TemporaryFile(mode="w+") as stderr_log:
            process = subprocess.Popen(
                [str(binary), "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_log,
                text=True,
                env=environment,
            )
            try:
                send_message(
                    process,
                    {
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "clientInfo": {
                                "name": "sudhir_ci_artifact",
                                "title": "Sudhir CI artifact check",
                                "version": "1.0.0",
                            },
                            "capabilities": {"experimentalApi": True},
                        },
                    },
                )
                read_response(process, 1)
                send_message(process, {"method": "initialized"})
                send_message(
                    process,
                    {
                        "id": 2,
                        "method": "config/batchWrite",
                        "params": {
                            "edits": [
                                {
                                    "keyPath": "model",
                                    "value": "pi-deepseek/v4-flash",
                                    "mergeStrategy": "replace",
                                },
                                {
                                    "keyPath": "model_reasoning_effort",
                                    "value": "ultra",
                                    "mergeStrategy": "replace",
                                },
                            ],
                            "reloadUserConfig": False,
                        },
                    },
                )
                write_response = read_response(process, 2)
                write_result = write_response["result"]
                if not isinstance(write_result, dict) or write_result.get("status") != "ok":
                    raise AssertionError(
                        f"config/batchWrite did not return status=ok: {write_response}"
                    )

                send_message(
                    process,
                    {
                        "id": 3,
                        "method": "config/read",
                        "params": {"includeLayers": False},
                    },
                )
                read_result = read_response(process, 3)["result"]
                if not isinstance(read_result, dict):
                    raise AssertionError(f"invalid config/read result: {read_result}")
                config = read_result.get("config")
                expected = {
                    "model": "pi-deepseek/v4-flash",
                    "model_reasoning_effort": "ultra",
                }
                if not isinstance(config, dict):
                    raise AssertionError(f"invalid config/read config: {config}")
                actual = {key: config.get(key) for key in expected}
                if actual != expected:
                    raise AssertionError(
                        f"new-task defaults were not persisted: expected={expected}, actual={actual}"
                    )
            except Exception:
                stderr_log.seek(0)
                diagnostics = stderr_log.read()
                if diagnostics:
                    print(diagnostics, end="", file=sys.stderr)
                raise
            finally:
                if process.stdin is not None:
                    process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    arguments = parser.parse_args()
    verify(arguments.binary)
    print(f"verified model-picker persistence: {arguments.binary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
