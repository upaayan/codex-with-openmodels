# dsh Cursor overflow and Cursor-to-xAI model-switch

Gateway-side record of the 2026-08-26 dsh/gateway repair. The debate-loop
implementation and audit live in the Alamelu checkout:

`/Users/sudhirjha/playground/alamelu/documents/plan-audit-implementation/dsh_cursor_overflow_and_model_switch_implementation.md`

`/Users/sudhirjha/playground/alamelu/documents/plan-audit-implementation/dsh_cursor_overflow_and_model_switch_audit.md`

## Scope

- Report Cursor OpenAI `prompt_tokens` from the prompt sent to Cursor, not
  Cursor SDK agent-loop `inputTokens`.
- Accept untyped `{role, content}` items on the openai-responses path so a
  dsh switch from Cursor Grok to xAI Grok works.
- Do not change `_gpt_response`.
- Align `~/.dsh/settings.yaml` Cursor `contextWindow` with `cursor_catalog.py`.

## Bug index

- `SC-BUG-2026-08-26-001`
- `SC-BUG-2026-08-26-002`
