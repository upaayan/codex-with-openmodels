# Sudhir-Codex bug list

This is the durable index of reproduced Sudhir-Codex defects and their repair
status. A fixed entry remains here so a later backend or gateway upgrade does
not accidentally reintroduce it.

## SC-BUG-2026-08-08-001 — GPT fails after a DeepSeek turn

- **Status:** Fixed and live verified on 2026-08-08; recurrence corrected,
  deployed, and live verified on macOS and WSL on 2026-08-20
- **Area:** Python gateway; cross-provider Responses history
- **Reproduction:** Start a task with `gpt-5.6-sol`, switch to
  `pi-deepseek/deepseek-v4-flash`, then switch back to `gpt-5.6-sol`.
- **Observed error:**

  ```text
  Invalid 'input[21].content': array too long. Expected an array with maximum
  length 0, but got an array with length 1 instead.
  ```

### Root cause

DeepSeek returned a reasoning history item with one plaintext `reasoning_text`
element in `content`. When the task switched back to GPT, the gateway forwarded
that foreign reasoning item unchanged. ChatGPT's Responses input schema accepts
GPT-owned opaque reasoning replay, but rejects a non-empty plaintext `content`
array on a reasoning input item.

The first repair assumed foreign plaintext reasoning had no encrypted payload.
That assumption became false: native DeepSeek Responses now emits the plaintext
`content` together with a non-empty, gateway-scoped carrier whose value begins
`sudhir-codex-route-v1.`. The old filter treated any non-empty
`encrypted_content` as GPT-safe, so the same invalid reasoning shape crossed
the GPT boundary again. The first regression covered only
`encrypted_content: null`, allowing this recurrence.

### Fix

The GPT forwarding path now calls `_gpt_compatible_request()` in
`sudhir_codex/src/sudhir_codex_gateway/app.py`. It removes every reasoning item
whose `content` is a non-empty list, regardless of whether
`encrypted_content` is absent, GPT-opaque, or a gateway-scoped foreign carrier.
It preserves visible assistant history, tool calls and results, and GPT-owned
encrypted reasoning items whose plaintext `content` is empty or absent. Stored
task history and open-model-to-open-model replay are unchanged.

The regression
`test_app.GatewayAppTests.test_gpt_request_omits_foreign_plaintext_reasoning`
lives in `sudhir_codex/tests/test_app.py`. It covers both the original null
carrier and a real `sudhir-codex-route-v1.` carrier, and remains a mandatory
member of the `gateway` lane in
`scripts/tests/sudhir_targeted_regressions.py`.

### Verification and deployment evidence

- The focused regression failed before the repair and passed after it.
- The explicit gateway lane passed in both source copies: 23/23 tests.
- Compile, Ruff, whitespace, and publication/private parity checks passed.
- The gateway restarted once from PID `1724` to healthy PID `27208`.
- Persisted smoke task `019fe1d2-6081-7042-91b1-17f65a8b83e7` completed:

  ```text
  gpt-5.6-sol                   -> SUDHIR_SWITCH_GPT_ONE
  pi-deepseek/deepseek-v4-flash -> SUDHIR_SWITCH_DEEPSEEK_TWO
  gpt-5.6-sol                   -> SUDHIR_SWITCH_GPT_THREE
  ```

All three commands exited zero, and the route journal recorded HTTP 200 for
the DeepSeek and return-to-GPT calls.

The 2026-08-20 recurrence was reproduced with the same validation error. Its
focused regression failed before the corrected filter and passed afterward;
Python compilation and whitespace checks also passed. The local gateway
restarted once from PID `1665` to healthy PID `68145`. Persisted task
`01a0203d-b952-7cf2-901b-f87c7324b531` recorded nine DeepSeek reasoning items
with `content_len: 1` and a `sudhir-codex-route-v1.` carrier, then returned to
Sol successfully with:

```text
LOCAL_FIX_ROUTE_TAGGED_SOL_OK
```

Both the DeepSeek turn and return-to-Sol turn recorded HTTP 200.

The WSL checkout received only the focused `app.py` and `test_app.py` changes;
its unrelated tracked modifications were preserved. The exact WSL regression
failed before the filter, showing that both the null-carrier and route-scoped
reasoning items reached GPT, then passed after the filter. Python compilation
and the focused whitespace check passed. The preimages are retained at:

```text
/home/ubuntu/.playground/sudhir-codex/dist/backups/route-tagged-gpt-20260820T175213Z
```

The Windows app closed normally from PID `7476`; its WSL app-server reached
zero processes. The private WSL gateway restarted once from PID `1987` to PID
`10577`. The final Windows app is running as PID `9788` with WSL app-server PID
`12017` using the expected deployed core. Doctor reported `OK`.

Persisted WSL task `01a02054-e4c1-7e40-ad01-03ba5e458c51` then produced one
DeepSeek reasoning item with non-empty plaintext `content` and a
`sudhir-codex-route-v1.` carrier. The same task returned to Sol successfully:

```text
WSL_ROUTE_TAGGED_DEEPSEEK_OK
WSL_ROUTE_TAGGED_SOL_OK
```

Both WSL routes recorded HTTP 200.

### Upgrade preservation requirement

Future gateway reconciliation, deployment manifests, and backend upgrades must
preserve these three pieces together:

1. `_gpt_compatible_request()` and its GPT-only call site in `app.py`;
2. `test_gpt_request_omits_foreign_plaintext_reasoning` in `test_app.py`; and
3. that exact test's membership in the targeted `gateway` lane.

The controlling maintenance requirement is also recorded as
`SC-REASONING-005` in `BACKEND-MONTHLY-UPGRADE.md`.

## SC-BUG-2026-08-26-001 — dsh Cursor turn marked CONTEXT_WINDOW_EXCEEDED

- **Status:** Fixed in source; gateway restart still required for live effect
- **Area:** Python gateway Cursor usage; dsh catalog `contextWindow`
- **Reproduction:** In dsh, use `cursor/grok-4.6-fast` or `cursor/grok-4.6-slow`
  against a large repo such as lazydata.
- **Observed error:**

  ```text
  pi-ai detected context overflow for model "cursor/grok-4.6-fast"
  CONTEXT_WINDOW_EXCEEDED
  ```

  The model had already returned `stop` with thousands of output tokens.

### Root cause

Two stacked mismatches:

1. dsh `~/.dsh/settings.yaml` still listed every Cursor model at 128000. The
   gateway catalog is 200000 for Composer and 256000 for Cursor Grok.
2. `_cursor_response` forwarded Cursor SDK `usage.inputTokens` as OpenAI
   `prompt_tokens`. That SDK field is the native agent-loop total (observed
   198k–930k), not the prompt the gateway sent (~3k). dsh/pi-ai then treats
   `stop` + `usage.input > contextWindow` as overflow.

This is not a Grok model failure and not a ChatGPT path defect.

### Fix

- `_cursor_response` reports `prompt_tokens` from
  `estimate_prompt_tokens(turn.prompt)`.
- dsh Cursor `contextWindow` values now match the gateway catalog.
- `_gpt_response` is unchanged.

Regressions:
`test_app.GatewayAppTests.test_cursor_request_uses_the_native_worker_and_returns_responses_sse`
and
`test_cursor_prompt.CursorPromptTests.test_estimate_prompt_tokens_uses_sent_text_not_agent_loop`.

## SC-BUG-2026-08-26-002 — dsh switch from Cursor Grok to xAI Grok fails

- **Status:** Fixed in source; gateway restart still required for live effect
- **Area:** Python gateway openai-responses translator
- **Reproduction:** In dsh, start on `cursor/grok-4.6-*`, then switch the same
  session to `pi-xai-i2o/grok-4.6`.
- **Observed error:**

  ```text
  OpenAI API error (400): {"message":"Responses input item type None is unsupported","type":"sudhir_codex_gateway_error","code":"unsupported_input_item"}
  INVALID_REQUEST
  ```

### Root cause

`adapter.py` already accepts pi-ai chat-style `{role, content}` items that omit
`type`. `openai_responses.py` did not. xAI Grok uses the openai-responses path,
so the switch failed. Typed ChatGPT Responses items were never the problem.

### Fix

`openai_responses._translate_input` treats `type is None` plus `role` and
`content` as a message, matching `adapter.py`. Unknown typed items still raise.

Regressions:
`test_openai_responses.OpenAIResponsesTests.test_chat_style_message_items_without_type_are_accepted`
and
`test_openai_responses.OpenAIResponsesTests.test_unknown_input_item_type_is_still_rejected`.
