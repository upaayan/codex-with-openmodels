---
card: canonical-artifacts
status: active
source: owner-confirmed
inject: always
updated: 2026-08-23
---
# Canonical Artifacts
- Operational truth is the private Sudhir backend/gateway plus private state — not the official Codex app. Overview (verified 2026-08-20): `documents/SUDHIR-CODEX-SYSTEM-OVERVIEW.md`. Repair: `documents/SUDHIR-CODEX-REPAIR-RUNBOOK.md`.
- `/Applications/Sudhir-Codex.app` is a small signed launcher for the official ChatGPT frontend with Sudhir-owned backend/config/state.
- Gateway source: `sudhir_codex/`. Deployed backend: `dist/sudhir-codex-core`. Fork contract: `scripts/tests/sudhir_fork_contracts.toml`.
- Backend procedure: `documents/BACKEND-MONTHLY-UPGRADE.md`. Frontend procedure is separate: `documents/FRONTEND-UPDATE.md`. Prefer the dated overview over older README "rc.12" wording.
