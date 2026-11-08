---
card: environment
status: active
source: owner-confirmed
updated: 2026-11-08
inject: on-match
match: ["Windows", "laptop", "S3", "relay", "32179", "wsl2", "sudhir-codex-core", ".sudhir-codex"]
---
# Environment
- Source: `/Users/sudhirjha/.playground/sudhir-codex`. Rust: `codex-rs`. Gateway: `sudhir_codex`. Deployed core: `dist/sudhir-codex-core`. CLI: `/Users/sudhirjha/.local/bin/sudhir-codex`. Private state: `~/.sudhir-codex`. Frontend project: `/Users/sudhirjha/.playground/sudhir-codex-app`.
- Gateway loopback `127.0.0.1:32179`. Release targets: macOS Apple Silicon and Linux x64 MUSL; Windows uses WSL2 Linux x64.
- Protected: official `~/.codex/`, private `~/.sudhir-codex/`, Pi auth, Cursor worker state, `dist/backups/`.

- Windows laptop connection: its own S3 relay to a WSL poller, distinct from the mock/backup relay buckets. WSL backend `~/.playground/sudhir-codex`, private state `~/.sudhir-codex`. Bucket, region, full protocol and paths: private `documents/BACKEND-MONTHLY-UPGRADE.md`, “Windows / WSL connection”.

- 2026-10-22 Mac accepted artifact/runtime 0.163.0: source 15e5751849d0954d99d08da0141978a02692a88e; Blacksmith run 39820837415, all five native probes/final marker PASS. Two startups and live GPT WebSocket/Pi HTTP/existing-history PASS; seven GPT choices preserved, actual/effective 0.163 with no override. Final Opus/high Round 12 PASS (0 high, 0 medium, 0 low). Reversal dist/backups/pre-sudhir-0163-20261022-catalog includes exact old 0.159 override and six DBs. External DevTools endpoint verified, Console/Urvashi visibility unverified. Legacy MCP-server removal owner-accepted. Official app/provider preserved; WSL unchanged. Authoritative status: `dist/release-evidence/in-progress/upgrade-0163-20261022/release-record.json`.
