import concurrent.futures
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sudhir_codex_gateway.cursor_worker import CursorWorkerClient
from sudhir_codex_gateway.cursor_worker import _find_node


class CursorWorkerClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.worker = self.root / "fake-worker.py"
        self.worker.write_text(
            (
                "import json, os, sys\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    print(json.dumps({\n"
                "        'id': request['id'],\n"
                "        'ok': True,\n"
                "        'text': request['model'] + ':' + request['prompt'] + ':' + os.getcwd(),\n"
                "        'usage': {'inputTokens': 7, 'outputTokens': 3},\n"
                "        'toolCalls': 2,\n"
                "    }), flush=True)\n"
            ),
            encoding="utf-8",
        )
        self.worker.chmod(self.worker.stat().st_mode | stat.S_IXUSR)
        self.auth = self.root / "auth.json"
        self.auth.write_text(
            json.dumps({"cursor": {"type": "api_key", "key": "secret"}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_round_trips_one_ndjson_request_without_exposing_auth(self) -> None:
        client = CursorWorkerClient(
            worker_script=self.worker,
            state_dir=self.root / "state",
            auth_path=self.auth,
            node_binary=Path(sys.executable),
            environment={"PATH": os.environ.get("PATH", "")},
        )
        try:
            result = client.turn(
                model_id="cursor/composer-2.5-fast",
                cwd=self.workspace,
                prompt="hello",
                thread_id="thread-1",
            )
        finally:
            client.close()

        prefix = "cursor/composer-2.5-fast:hello:"
        self.assertTrue(result.text.startswith(prefix))
        self.assertTrue(
            os.path.samefile(result.text.removeprefix(prefix), self.workspace),
        )
        self.assertEqual(result.input_tokens, 7)
        self.assertEqual(result.output_tokens, 3)
        self.assertEqual(result.tool_calls, 2)
        self.assertNotIn("secret", result.text)

    def test_six_concurrent_agents_keep_their_own_working_directories(
        self,
    ) -> None:
        workspaces = [self.root / f"agent-{index}" for index in range(6)]
        for workspace in workspaces:
            workspace.mkdir()
        client = CursorWorkerClient(
            worker_script=self.worker,
            state_dir=self.root / "state",
            auth_path=self.auth,
            node_binary=Path(sys.executable),
            environment={"PATH": os.environ.get("PATH", "")},
        )

        def run(workspace: Path) -> str:
            return client.turn(
                model_id="cursor/composer-latest-fast",
                cwd=workspace,
                prompt=workspace.name,
                thread_id=workspace.name,
            ).text

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(run, workspaces))
        finally:
            client.close()

        for workspace, result in zip(workspaces, results, strict=True):
            prefix = f"cursor/composer-latest-fast:{workspace.name}:"
            self.assertTrue(result.startswith(prefix))
            self.assertTrue(
                os.path.samefile(result.removeprefix(prefix), workspace),
            )

    def test_configured_node_binary_precedes_path_lookup(self) -> None:
        configured = Path(sys.executable)
        with (
            mock.patch.dict(
                os.environ,
                {"SUDHIR_CODEX_NODE": str(configured)},
            ),
            mock.patch(
                "sudhir_codex_gateway.cursor_worker.shutil.which",
                return_value=str(self.root / "path-node"),
            ),
        ):
            self.assertEqual(_find_node(), configured)

    def test_windows_node_executable_is_resolved_from_path(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch(
                "sudhir_codex_gateway.cursor_worker.shutil.which",
                return_value=sys.executable,
            ),
            mock.patch(
                "sudhir_codex_gateway.cursor_worker.is_windows",
                return_value=True,
            ),
        ):
            self.assertEqual(_find_node(), Path(sys.executable))


if __name__ == "__main__":
    unittest.main()
