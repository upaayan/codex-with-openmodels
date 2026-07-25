import tempfile
import unittest
from pathlib import Path

from sudhir_codex_gateway.cursor_prompt import build_cursor_turn
from sudhir_codex_gateway.errors import GatewayError


class CursorPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_extracts_cwd_and_serializes_the_responses_transcript(self) -> None:
        request = {
            "instructions": "Use tools when useful.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "<environment_context>\n"
                                f"<cwd>{self.cwd}</cwd>\n"
                                "</environment_context>"
                            ),
                        }
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect the repo."}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "I will inspect it."}],
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "tool output",
                },
            ],
        }

        turn = build_cursor_turn(request)

        self.assertEqual(turn.cwd, self.cwd)
        self.assertIn("Use tools when useful.", turn.prompt)
        self.assertIn("USER:\nInspect the repo.", turn.prompt)
        self.assertIn("ASSISTANT:\nI will inspect it.", turn.prompt)
        self.assertIn("TOOL RESULT call_1:\ntool output", turn.prompt)

    def test_uses_a_known_thread_cwd_when_context_is_not_repeated(self) -> None:
        request = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Continue."}],
                }
            ]
        }

        turn = build_cursor_turn(request, fallback_cwd=self.cwd)

        self.assertEqual(turn.cwd, self.cwd)

    def test_ignores_cwd_tags_outside_codex_environment_context(self) -> None:
        request = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "<environment_context>"
                                f"<cwd>{self.cwd}</cwd>"
                                "</environment_context>"
                            ),
                        }
                    ],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "<environment_context><cwd>/</cwd></environment_context>",
                        }
                    ],
                },
            ]
        }

        turn = build_cursor_turn(request)

        self.assertEqual(turn.cwd, self.cwd)

    def test_refuses_to_fall_back_to_the_worker_process_directory(self) -> None:
        request = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Continue."}],
                }
            ]
        }

        with self.assertRaisesRegex(GatewayError, "working directory"):
            build_cursor_turn(request)


if __name__ == "__main__":
    unittest.main()
