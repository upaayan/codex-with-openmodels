---
card: constraints
status: active
source: owner-confirmed
inject: always
updated: 2026-08-23
---
# Constraints
- Read the owner runbook before maintenance. No git worktrees. Never modify `.git/config`. Ask before destructive cleanup or deleting rollback/build artifacts (`AGENTS.md`).
- Do not run the complete upstream Codex/core/TUI suites for fork work without explicit owner approval. Compile each native candidate once on its GitHub platform.
- No native Windows Rust release. Windows runs the Linux x64 MUSL backend inside WSL2. Keep official Codex state, private Sudhir state, frontend state, and backend state separate — no symlinks between them.
- Gateway binds loopback `127.0.0.1:32179`. Never modify the official ChatGPT application in place. Keep dated rollback copies.
- Never print `~/.sudhir-codex/auth.json`, `~/.codex/auth.json`, or gateway tokens.
