#!/usr/bin/env python3
"""Exercise SC-AGENTS-001 on a built core with local Responses fixtures, no auth/Cargo."""

import argparse
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROUTES = ("gpt-6-astra", "pi-deepseek/deepseek-v4-flash", "cursor/composer-2.5")
CHILD = "SUDHIR_AGENT_PLAINTEXT_NONCE"
TOOL = "SUDHIR_AGENT_TOOL_OK"


def verify_pair(binary: Path, parent: str, child: str, fork: str) -> None:
    requests = []
    child_done = threading.Event()
    stage = 0
    root_thread = None
    child_requests = []
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal stage, root_thread
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            inputs = body.get("input", [])
            thread_id = self.headers.get("thread-id")
            if root_thread is None:
                root_thread = thread_id
            is_child = thread_id != root_thread
            messages = [
                x
                for x in inputs
                if x.get("type") == "agent_message"
                and x.get("recipient") == "/root/worker"
            ]
            number = len(requests)

            def call(name, arguments, namespace=None):
                item = {
                    "type": "function_call",
                    "id": f"fc{number}",
                    "call_id": f"call{number}",
                    "name": name,
                    "arguments": json.dumps(arguments),
                }
                if namespace:
                    item["namespace"] = namespace
                return item

            def reply(text):
                return {
                    "type": "message",
                    "id": f"m{number}",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }

            if is_child:
                child_requests.append(body)
                if not messages:
                    failures.append("child request has no addressed agent message")
                if body["model"] != child:
                    failures.append(
                        f"child routed to {body['model']}, expected {child}"
                    )
                for message in messages:
                    content = message.get("content", [])
                    if not content or any(
                        x.get("type") != "input_text" for x in content
                    ):
                        failures.append(f"non-plaintext agent content: {content}")
                    if not any(CHILD in x.get("text", "") for x in content):
                        failures.append("child message lost plaintext nonce")
                last_message = max(
                    i for i, x in enumerate(inputs) if x.get("type") == "agent_message"
                )
                outputs = [
                    x
                    for x in inputs[last_message + 1 :]
                    if x.get("type") == "function_call_output"
                ]
                if not outputs:
                    item = call(
                        "exec_command",
                        {"cmd": f"printf {TOOL}", "max_output_tokens": 100},
                    )
                else:
                    if TOOL not in json.dumps(outputs):
                        failures.append("child exec_command output was not usable")
                    item = reply("SUDHIR_CHILD_DONE")
                    child_done.set()
            elif stage == 0:
                stage = 1
                item = call(
                    "spawn_agent",
                    {
                        "task_name": "worker",
                        "model": child,
                        "fork_turns": fork,
                        "message": CHILD + ":spawn",
                    },
                    "sudhir_agents",
                )
            elif stage == 1:
                if not child_done.wait(15):
                    failures.append("spawned child did not finish its tool call")
                child_done.clear()
                stage = 2
                item = call(
                    "send_message",
                    {"target": "worker", "message": CHILD + ":send"},
                    "sudhir_agents",
                )
            elif stage == 2:
                stage = 3
                item = call(
                    "followup_task",
                    {"target": "worker", "message": CHILD + ":followup"},
                    "sudhir_agents",
                )
            else:
                if not child_done.wait(15):
                    failures.append("followup child did not finish its tool call")
                item = reply("SUDHIR_PARENT_DONE")

            events = [
                {"type": "response.created", "response": {"id": f"r{number}"}},
                {"type": "response.output_item.done", "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": f"r{number}",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 10,
                            "total_tokens": 20,
                        },
                    },
                },
            ]
            encoded = "".join(
                "data: " + json.dumps(x) + "\n\n" for x in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="sudhir-agents-artifact-") as directory:
            home = Path(directory) / "codex-home"
            home.mkdir()
            catalog = json.loads(
                (ROOT / "codex-rs/models-manager/models.json").read_text()
            )
            seed = next(x for x in catalog["models"] if x["slug"] == "gpt-5.5")
            models = [
                {
                    **seed,
                    "slug": slug,
                    "display_name": slug,
                    "use_responses_lite": False,
                    "visibility": "list",
                }
                for slug in set(ROUTES + (child,))
            ]
            (home / "models.json").write_text(json.dumps({"models": models}))
            (home / "config.toml").write_text(
                f'model={json.dumps(parent)}\nmodel_provider="fixture"\n'
                f"model_catalog_json={json.dumps(str(home / 'models.json'))}\n"
                "check_for_update_on_startup=false\n"
                "[features]\nenable_request_compression=false\nplugins=false\n"
                '[features.multi_agent_v2]\nenabled=true\ntool_namespace="sudhir_agents"\n'
                "[agents]\nenabled=true\nmax_concurrent_threads_per_session=6\n"
                '[model_providers.fixture]\nname="Local fixture"\n'
                f'base_url="http://127.0.0.1:{server.server_port}/v1"\n'
                'wire_api="responses"\nrequires_openai_auth=false\n'
            )
            os_home = Path(directory) / "os-home"
            os_home.mkdir()
            environment = {**os.environ, "CODEX_HOME": str(home), "HOME": str(os_home)}
            for name in (
                "OPENAI_API_KEY",
                "CODEX_API_KEY",
                "SUDHIR_CODEX_GATEWAY_TOKEN",
            ):
                environment.pop(name, None)
            output = home / "final.txt"
            result = subprocess.run(
                [
                    str(binary),
                    "exec",
                    "--skip-git-repo-check",
                    "-s",
                    "danger-full-access",
                    "--json",
                    "-o",
                    str(output),
                    "SUDHIR_PARENT_START",
                ],
                cwd=home,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
            )
            if (
                result.returncode != 0
                or not output.exists()
                or output.read_text().strip() != "SUDHIR_PARENT_DONE"
            ):
                failures.append(
                    f"parent failed: {result.stdout[-2000:]} {result.stderr[-2000:]}"
                )
            if root_thread is None:
                failures.append("missing thread-id request header")
            if len(child_requests) < 4 or stage != 3:
                failures.append(
                    "spawn, send, followup and two child tool loops were not exercised: "
                    + result.stdout[-6000:]
                    + result.stderr[-2000:]
                )
            if fork == "3" and not any(
                "SUDHIR_PARENT_START" in json.dumps(x) for x in child_requests
            ):
                failures.append("requested parent history was not inherited")
            delivered = {
                part.get("text")
                for request in child_requests
                for item in request.get("input", [])
                if item.get("type") == "agent_message"
                and item.get("recipient") == "/root/worker"
                for part in item.get("content", [])
                if part.get("type") == "input_text"
            }
            expected_messages = {
                CHILD + ":" + operation for operation in ("spawn", "send", "followup")
            }
            if not expected_messages <= delivered:
                failures.append(
                    "spawn/send/followup plaintext messages were not all delivered intact"
                )
            if fork == "none" and any(
                "SUDHIR_PARENT_START" in json.dumps(x) for x in child_requests
            ):
                failures.append("fresh child unexpectedly inherited parent history")
            if failures:
                raise AssertionError("; ".join(dict.fromkeys(failures)))
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument(
        "--single", action="store_true", help="diagnose the GPT to GPT inherited case"
    )
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    pairs = [
        (parent, child, fork)
        for parent in ROUTES
        for child in ROUTES
        for fork in ("none", "3")
    ]
    # Different GPT child is required in addition to all provider-class pairs.
    pairs.extend(("gpt-6-astra", "gpt-5.6-sol", fork) for fork in ("none", "3"))
    if args.single:
        pairs = [("gpt-6-astra", "gpt-5.6-sol", "3")]
    failed = False
    for parent, child, fork in pairs:
        try:
            verify_pair(binary, parent, child, fork)
            print(
                json.dumps(
                    {"parent": parent, "child": child, "fork": fork, "status": "pass"}
                ),
                flush=True,
            )
        except Exception as error:
            failed = True
            print(
                json.dumps(
                    {
                        "parent": parent,
                        "child": child,
                        "fork": fork,
                        "status": "fail",
                        "error": str(error),
                    }
                ),
                flush=True,
            )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
