---
card: canonical-artifacts
status: active
source: owner-confirmed
inject: always
updated: 2026-11-08
---
# Canonical Artifacts
- Operational truth is the private Sudhir backend/gateway plus private state — not the official Codex app. Overview (verified 2026-08-20): `documents/SUDHIR-CODEX-SYSTEM-OVERVIEW.md`. Repair: `documents/SUDHIR-CODEX-REPAIR-RUNBOOK.md`.
- `/Applications/Sudhir-Codex.app` is a small signed launcher for the official ChatGPT frontend with Sudhir-owned backend/config/state.
- Gateway source: `sudhir_codex/`. Deployed backend: `dist/sudhir-codex-core`. Fork contract: `scripts/tests/sudhir_fork_contracts.toml`.
- Backend procedure: `documents/BACKEND-MONTHLY-UPGRADE.md`. Frontend procedure is separate: `documents/FRONTEND-UPDATE.md`. Prefer the dated overview over older README "rc.12" wording.

- Windows/WSL access uses its own S3 relay bucket (not SSH or the server buckets). Find the bucket and protocol in `documents/BACKEND-MONTHLY-UPGRADE.md` in the private backend checkout.

- Backend release verified 2026-09-05: Mac and WSL core `0.153.4`, rc17 (`e94f6015`), gateway actual/effective `0.153.4`, no override. Matched rollback was backend `dist/backups/pre-openmodels-v0.1.0-rc.17-20260905` (2026-11-08: Mac copy no longer exists; WSL not checked). Detailed evidence and GUI/Browser limits: private backend `documents/plan-audit-implementation/sudhir_codex_0153_upgrade_implementation.md`.

- 2026-10-22 Mac accepted artifact/runtime 0.163.0: source 15e5751849d0954d99d08da0141978a02692a88e; Blacksmith run 39820837415, all five native probes/final marker PASS. Two startups and live GPT WebSocket/Pi HTTP/existing-history PASS; seven GPT choices preserved, actual/effective 0.163 with no override. Final Opus/high Round 12 PASS (0 high, 0 medium, 0 low). Reversal dist/backups/pre-sudhir-0163-20261022-catalog includes exact old 0.159 override and six DBs. External DevTools endpoint verified, Console/Urvashi visibility unverified. Legacy MCP-server removal owner-accepted. Official app/provider preserved; WSL unchanged. Authoritative status: `dist/release-evidence/in-progress/upgrade-0163-20261022/release-record.json`.

- 2026-11-07 Mac gateway Pi SDK **1.5.0** activated with case-insensitive nullable OAuth auth-header deletion and Pi-aligned xAI Responses defaults; explicit API overrides preserved. Cursor SDK stays **1.0.23**, core/catalog **0.163.0**. Opus 5.5/xhigh implementation Round 2 PASS, no open findings. Source/package/lock/tests/upgrade-manifest unit committed to public main on 2026-11-08 (Sudhir); no WSL deployment claimed. Scoped rollback: `dist/backups/20261107-pi-150` with intact old node_modules and source preimages; preserve newly refreshed credentials. Evidence: `documents/plan-audit-implementation/pi_150_gateway_implementation.md`.

- 2026-11-08 Sudhir-Codex installs on the backup server (retired 26 Sep) and mock server (retired 8 Nov) are removed; do not recreate them. Running Mac core source `15e5751849d0` is on origin as branch `sudhir/blacksmith-0163-mac-20261022`. Upgrade branches stay review-only and are not merged into main. Detail: private `documents/BACKEND-MONTHLY-UPGRADE.md`.
