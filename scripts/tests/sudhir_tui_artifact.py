#!/usr/bin/env python3
"""Exercise existing picker contracts through an already-built CLI and a PTY."""

import argparse
import codecs
import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time
import tomllib
from pathlib import Path

import pyte


def verify(binary: Path, repo: Path):
    catalog = json.loads((repo / "codex-rs/models-manager/models.json").read_text())
    seed = next(model for model in catalog["models"] if model["slug"] == "gpt-5.5")
    with tempfile.TemporaryDirectory(prefix="sudhir-tui-artifact-") as temp, tempfile.TemporaryDirectory(prefix="sudhir-tui-os-home-") as os_home:
        home = Path(temp).resolve()
        models = []
        for slug, title in [("pi-fixture/alpha", "Alpha provider"), ("pi-fixture/needle", "Needle Blue provider")]:
            models.append({**seed, "slug": slug, "display_name": title, "description": title,
                           "visibility": "list", "default_reasoning_level": "medium",
                           "supported_reasoning_levels": [{"effort": level, "description": level} for level in ("medium", "max", "ultra")]})
        (home / "models.json").write_text(json.dumps({"models": models}))
        config = ('model="pi-fixture/alpha"\nmodel_reasoning_effort="medium"\n'
                  'model_provider="fixture"\nmodel_catalog_json=' + json.dumps(str(home / "models.json")) + '\n'
                  'check_for_update_on_startup=false\napproval_policy="never"\nsandbox_mode="danger-full-access"\n'
                  '[features]\nplugins=false\n'
                  '[model_providers.fixture]\nname="Fixture"\nbase_url="http://127.0.0.1:1/v1"\n'
                  'wire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n'
                  '[projects.' + json.dumps(str(home)) + ']\ntrust_level="trusted"\n')
        (home / "config.toml").write_text(config)
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        environment = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "TMPDIR", "USER"}}
        environment.update(HOME=str(Path(os_home).resolve()), CODEX_HOME=str(home), TERM="xterm-256color", NO_COLOR="1")
        screen = pyte.Screen(120, 40)
        screen.write_process_input = lambda data: os.write(master, data.encode())
        stream, decoder = pyte.Stream(screen), codecs.getincrementaldecoder("utf-8")("replace")

        def child_terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        process = subprocess.Popen([str(binary), "--no-alt-screen"], cwd=home, env=environment,
                                   stdin=slave, stdout=slave, stderr=slave, preexec_fn=child_terminal)
        os.close(slave)

        def display():
            return "\n".join(screen.display)

        def wait_for(predicate, label):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if predicate(display()):
                    return display()
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    stream.feed(decoder.decode(data))
                if process.poll() is not None:
                    break
            raise AssertionError(f"{label} not established; exit={process.poll()}\n{display()}")

        def type_text(value):
            for char in value:
                os.write(master, bytes([char]))
                time.sleep(0.08)

        try:
            wait_for(lambda text: "pi-fixture/alpha" in text or "Alpha provider" in text, "private CLI startup")
            type_text(b"/model")
            os.write(master, b"\r")
            wait_for(lambda text: "Select Model" in text, "model picker")
            type_text(b"needle")
            filtered = wait_for(lambda text: "Needle Blue" in text and "Alpha provider" not in text, "searchable provider picker")
            assert "Alpha provider" not in filtered, filtered
            os.write(master, b"\r")
            effort = wait_for(lambda text: "Select Reasoning" in text and "Max" in text and "Ultra" in text, "unified Max/Ultra choices")
            assert "More reasoning" not in effort, effort
            os.write(master, b"\x1b[B\r")
            wait_for(lambda text: "Select Reasoning" not in text and "max" in text.lower(), "keyboard selection of Max")
            os.write(master, b"\x1b.")
            wait_for(lambda text: "ultra" in text.lower() and "Select Reasoning" not in text, "Alt+. reaches Ultra")
            defaults = tomllib.loads((home / "config.toml").read_text())
            assert (defaults["model"], defaults["model_reasoning_effort"]) == ("pi-fixture/alpha", "medium"), "Active picker changed new-task defaults"
            return {"SC-TUI-001": "pass", "SC-TUI-002": "pass", "active_task_defaults_unchanged": True}
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
            os.close(master)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("repo", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.binary.resolve(), args.repo.resolve()), indent=2))
