#!/usr/bin/env python3
"""Verify compiled model routing/fallback using the existing loopback fixtures."""

import argparse
from dataclasses import replace
import json
import sys
import tempfile
from pathlib import Path

import httpx

from sudhir_core_artifact import RPC

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "sudhir_codex/src"), str(ROOT / "sudhir_codex/tests")]
from helpers import write_json
from test_websocket_relay import WebSocketRelayTests
from sudhir_codex_gateway.cursor_worker import CursorTurnResult


def response_events(text, response_id):
    return [
        {"type": "response.created", "response": {"id": response_id}},
        {"type": "response.output_item.done", "item": {"type": "message", "role": "assistant", "id": "msg_" + response_id,
                                                      "content": [{"type": "output_text", "text": text}]}},
        {"type": "response.completed", "response": {"id": response_id, "usage": {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105}}},
    ]


def verify(binary: Path, repo: Path, baseline=False):
    fixture = WebSocketRelayTests()
    fixture.setUp()
    try:
        writes, pi_requests, cursor_requests = [], [], []
        failure = [False]

        def native_events(payload):
            writes.append(payload)
            return response_events("WS_OK", "resp_" + str(len(writes)))

        fixture.response_events = native_events
        write_json(fixture.app.settings.pi_auth_path, {"demo": {"type": "api_key", "key": "fixture-pi-key"}})

        def http_route(request):
            payload = json.loads(request.content)
            if request.url.host == "pi.test":
                pi_requests.append(payload)
                if failure[0]:
                    return httpx.Response(500, json={"error": {"message": "fixture retry exhaustion"}})
                return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "PI_OK"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 5}})
            assert request.url.path.endswith("/responses"), request.url
            events = response_events("HTTP_FALLBACK_OK", "resp_http")
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=httpx.ByteStream("".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()))

        fixture.app.client.close()
        fixture.app.client = httpx.Client(transport=httpx.MockTransport(http_route))

        class CursorFixture:
            def turn(self, **kwargs):
                cursor_requests.append(kwargs)
                return CursorTurnResult("CURSOR_OK", 100, 5, 0)

            def close(self):
                pass

        fixture.app.cursor_worker = CursorFixture()
        fixture.app.settings = replace(fixture.app.settings, cursor_default_cwd=fixture.app.settings.repo_root)
        seed = next(model for model in json.loads((repo / "codex-rs/models-manager/models.json").read_text())["models"] if model["slug"] == "gpt-5.5")
        with tempfile.TemporaryDirectory(prefix="sudhir-ws-artifact-") as temp:
            home = Path(temp)
            write_json(home / "models.json", {"models": [
                {**seed, "slug": model, "display_name": model, "comp_hash": None, "use_responses_lite": False}
                for model in ("gpt-fixture", "pi-demo/demo/model", "cursor/composer-2.5-slow")
            ]})
            (home / "config.toml").write_text(
                'model="gpt-fixture"\nmodel_provider="fixture"\nmodel_catalog_json=' + json.dumps(str(home / "models.json")) + '\n'
                'check_for_update_on_startup=false\n[features]\nplugins=false\n'
                '[model_providers.fixture]\nname="Sudhir Gateway"\nwire_api="responses"\n'
                'base_url="http://127.0.0.1:' + str(fixture.gateway.server_address[1]) + '/v1"\n'
                'requires_openai_auth=false\nsupports_websockets=' + str(not baseline).lower() + '\n'
                'env_key="SUDHIR_WS_FIXTURE_KEY"\nrequest_max_retries=0\nstream_max_retries=0\n'
                '[model_providers.fixture.http_headers]\n"X-Sudhir-Gateway-Token"="gateway-secret"\n'
            )
            rpc = RPC(binary, home, extra_env={"SUDHIR_WS_FIXTURE_KEY": "chatgpt-fixture"})
            try:
                thread = rpc.request("thread/start", {"model": "gpt-fixture", "cwd": str(home), "approvalPolicy": "never", "sandbox": "danger-full-access"})["thread"]["id"]
                for model in ("gpt-fixture", "pi-demo/demo/model", "cursor/composer-2.5-slow", "gpt-fixture"):
                    rpc.turn(thread, model, "Reply with the fixture text")
                assert len([item for item in writes if item.get("generate") is not False]) == (0 if baseline else 2), writes
                assert len(pi_requests) == 1 and len(cursor_requests) == 1, (pi_requests, cursor_requests)
                failure[0] = True
                rpc.turn(thread, "pi-demo/demo/model", "Trigger the fixture HTTP failure", expected_status="failed")
                before = len(writes)
                rpc.turn(thread, "gpt-fixture", "GPT still uses WebSockets")
                if not baseline:
                    assert len(writes) > before, "An HTTP-only model failure disabled later GPT WebSockets"
            finally:
                rpc.close()
        # A new session sees a real upstream 426 and must use HTTP fallback.
        fixture.reject = 426
        with tempfile.TemporaryDirectory(prefix="sudhir-ws-fallback-") as temp:
            fallback_home = Path(temp)
            write_json(fallback_home / "models.json", {"models": [{**seed, "slug": "gpt-fixture", "display_name": "gpt-fixture", "use_responses_lite": False}]})
            (fallback_home / "config.toml").write_text(
                'model="gpt-fixture"\nmodel_provider="fixture"\nmodel_catalog_json=' + json.dumps(str(fallback_home / "models.json")) + '\n'
                'check_for_update_on_startup=false\n[features]\nplugins=false\n'
                '[model_providers.fixture]\nname="Sudhir Gateway"\nwire_api="responses"\n'
                'base_url="http://127.0.0.1:' + str(fixture.gateway.server_address[1]) + '/v1"\n'
                'requires_openai_auth=false\nsupports_websockets=' + str(not baseline).lower() + '\nenv_key="SUDHIR_WS_FIXTURE_KEY"\n'
                '[model_providers.fixture.http_headers]\n"X-Sudhir-Gateway-Token"="gateway-secret"\n'
            )
            rpc = RPC(binary, fallback_home, extra_env={"SUDHIR_WS_FIXTURE_KEY": "chatgpt-fixture"})
            try:
                thread = rpc.request("thread/start", {"model": "gpt-fixture", "cwd": str(fallback_home), "approvalPolicy": "never", "sandbox": "danger-full-access"})["thread"]["id"]
                rpc.turn(thread, "gpt-fixture", "Use HTTP after upstream 426")
                assert "HTTP_FALLBACK_OK" in json.dumps(rpc.events), rpc.events
            finally:
                rpc.close()
        if baseline:
            return {"shared_http_routing_harness": "pass", "native_websockets": "not-run-baseline"}
        return {"gpt_pi_cursor_gpt": "pass", "http_failure_preserves_gpt_ws": "pass", "upstream_426_http_fallback": "pass"}
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.binary.resolve(), args.repo.resolve(), args.baseline), indent=2))
