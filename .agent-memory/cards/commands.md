---
card: commands
status: active
source: owner-confirmed
updated: 2026-08-23
inject: on-match
match: ["sudhir-codex", "gateway", "just ", "fork_contracts", "install-sudhir"]
---
# Commands
- CLI: `sudhir-codex models`, `sudhir-codex doctor`, `sudhir-codex gateway status|start|stop|rotate-token`, `sudhir-codex mcp import`.
- Installer: `./scripts/install-sudhir-codex --archive <archive> --checksums <SHA256SUMS>`.
- Just: `just fmt`, `just test -p <crate>`, `just clippy` (from the justfile; `codex-rs` is the Rust cwd).
- Targeted fork checks: `python scripts/tests/verify_sudhir_fork_contracts.py lint --platform linux`; `python scripts/tests/sudhir_targeted_regressions.py gateway|rust`.
- Frontend status is a **separate** checkout command in the sudhir-codex-app runbook — not an authorize-to-replace signal.
