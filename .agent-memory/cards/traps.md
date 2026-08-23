---
card: traps
status: active
source: owner-confirmed
updated: 2026-08-23
inject: on-match
match: ["rc.12", "update-available", "catalog", "symlink", "thread-import", "frontend"]
---
# Traps
- Older README "current stable" wording can lag the 2026-08-20 overview (rc.13 backend). Prefer the dated runbook.
- A model visible in the catalog is not the same as a fresh-task selection being persisted.
- `frontend-status` saying update-available is diagnostic, not authorization to replace the known-good frontend.
- Backend and frontend updates are independent.
- Task-history import must copy (not symlink), normalize provider metadata, rebuild the private index, and leave official state untouched.
