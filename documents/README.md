# Sudhir-Codex maintenance runbooks

These runbooks preserve the working build, packaging, test, signing, and
rollback procedures for Sudhir-Codex. They are written so that Luna or another
agent can perform a future update without rediscovering the architecture.

Read these instructions before doing any maintenance:

1. `/Users/sudhirjha/.codex/AGENTS.md`
2. `/Users/sudhirjha/.playground/sudhir-codex/AGENTS.md`
3. The applicable runbook below

The owner's current instructions always control. Verification does not require
asking questions. Ask before changing something only when the intended change
is genuinely unclear, and always ask before deleting build artifacts or
rollback copies unless deletion was already explicitly approved.

## Runbooks

- [Bug list](./bugs/BUGLIST.md) records reproduced defects, their fixes, live
  verification, and the invariants that future upgrades must preserve.
- [Targeted testing policy](./TARGETED-TESTING.md) separates gateway/config,
  Rust binary, and frontend validation and records where each check belongs.
- [Monthly backend upgrade](./BACKEND-MONTHLY-UPGRADE.md) covers the open-source
  Codex Rust backend, the Python gateway, model adapters, tests, release
  compilation, stripping, deployment, runtime validation, and rollback.
- [Frontend update](./FRONTEND-UPDATE.md) covers the current signed small launcher,
  official ChatGPT compatibility, stable Computer Use recovery and upgrade gates.
  Earlier full-app clone/sign procedures are explicitly historical.
- [Rapid repair](./SUDHIR-CODEX-REPAIR-RUNBOOK.md) provides current paths and
  symptom-specific commands; start there for launcher failures.
- [Official Codex task import](./THREAD-IMPORT.md) covers copying task history
  into the private Sudhir-Codex state, preserving existing tasks, normalizing
  provider metadata, rebuilding the private index, verification, and rollback.
- [Reasoning continuity implementation](./plan-audit-implementation/sudhir_codex_reasoning_continuity_implementation.md)
  records the DeepSeek root cause, cross-provider reasoning preservation,
  atomic assistant replay invariants, diagnostics, and mandatory upgrade
  regressions.
- [DeepSeek Responses schema implementation](./plan-audit-implementation/sudhir_codex_deepseek_responses_schema_implementation.md)
  records the provider-specific root-`oneOf` tool-schema normalization that
  keeps deferred `codex_app` tools usable through direct DeepSeek Responses.
- [Native controls implementation](./plan-audit-implementation/sudhir_native_controls_implementation.md)
  records the completed and installed in-app Browser, Chrome, and Computer Use
  controls, their live verification, and the model-independent Computer Use
  bootstrap rule.
- [Targeted regression plan](./plan-audit-implementation/sudhir_codex_targeted_regression_plan.md)
  is the authoritative test selection for model-switch compaction, historical
  tools, and reasoning continuity. It forbids broad upstream suites unless the
  owner explicitly requests one.
- [Original implementation record](./plan-audit-implementation/sudhir_codex_implementation.md)
  contains the measured first non-LTO release, complete initial smoke matrix,
  and isolation audit. It is historical evidence; use the two maintenance
  runbooks above for future build work and the import runbook for task migration.

The two update streams are independent:

- A gateway/catalog-only change normally needs Python tests and a gateway
  restart, but no Rust build and no frontend rebuild.
- A Rust backend change needs one release build, but the desktop app does not
  need to be rebuilt because it launches the external deployed backend.
- An official ChatGPT update uses the installed small Sudhir launcher and
  official app. Run the current compatibility/provisioning checks; no clone,
  app rebuild or Rust build is required for the Computer Use template repair.

## Fixed installation paths

| Purpose | Path |
|---|---|
| Fork source | `/Users/sudhirjha/.playground/sudhir-codex` |
| Rust workspace | `/Users/sudhirjha/.playground/sudhir-codex/codex-rs` |
| Python gateway | `/Users/sudhirjha/.playground/sudhir-codex/sudhir_codex` |
| Cursor SDK worker | `/Users/sudhirjha/.playground/sudhir-codex/sudhir_codex/cursor_worker` |
| Cursor worker state | `/Users/sudhirjha/.sudhir-codex/cursor-sdk` |
| Deployed backend | `/Users/sudhirjha/.playground/sudhir-codex/dist/sudhir-codex-core` |
| CLI launcher | `/Users/sudhirjha/.local/bin/sudhir-codex` |
| Private Codex state | `/Users/sudhirjha/.sudhir-codex` |
| Frontend project | `/Users/sudhirjha/.playground/sudhir-codex-app` |
| Installed small launcher | `/Applications/Sudhir-Codex.app` |
| Frontend icon master | `/Users/sudhirjha/.playground/sudhir-codex-app/Resources/SudhirCodexIcon.png` |
| Electron profile | `/Users/sudhirjha/Library/Application Support/Sudhir-Codex-ChatGPT` |
| Official app | `/Applications/ChatGPT.app` |
| Official Codex state | `/Users/sudhirjha/.codex` |

There are deliberately no symlinks between official Codex and Sudhir-Codex.
Do not replace copied authentication, state, skills, executables, or app
resources with symlinks.

## Current frontend and Computer Use boundary — 2026-09-04

The signed `/Applications/Sudhir-Codex.app` launcher (`1.0.0` / build `1` at
verification) opens official ChatGPT (`26.901.22334` / `7746` at verification)
against `~/.sudhir-codex` and the `Sudhir-Codex-ChatGPT` profile. The stable skill
is `~/.sudhir-codex/skills/computer-use/SKILL.md`; owned recovery is under
`~/.sudhir-codex/control/plugin-assets`, `control/original-plugins`, and
`control/original-plugin-*`. The preserved IPC-2 runtime remains under
`~/.sudhir-codex-chatgpt/frontend-control-runtime/legacy-cua`.

Use [the current frontend procedure](./FRONTEND-UPDATE.md) for the exact template
priority, cache-layout rules, and five required upgrade regressions. Preserve the
source repair and test-gate membership together during a gateway/backend upgrade.

## Historical full-app installation — 2026-08-11

The following records the former cloned app; it is not the current installation.

- Working Sudhir-Codex: `26.727.51351` / build `6119`
- Bundle identifier: `com.sudhir.codex`
- Runtime identifier/CDHash: `com.sudhir.codex.runtime` /
  `bcc271f26306a5ded6e380268224821f5ec14ca6`
- Framework: `150.0.7871.182`
- Installed ASAR SHA-256:
  `45ac2b197f14bee42f3b5ead4d8056ae2022a105eac05029308ef1ce61604b72`
- Model-search experiment: absent
- Computer Use: live app enumeration, Sudhir-Codex accessibility tree, and
  screenshot capture passed
- Current official ChatGPT available locally: `26.803.41515` / build `6321`;
  it is newer but has not been adopted by Sudhir-Codex
- Sole retained full-app rollback:
  `/Users/sudhirjha/.playground/sudhir-codex-app/dist/backups/Sudhir-Codex.pre-model-search-poc-20260810-220749.app`

## Known-good backend release recorded on 2026-07-24

- Deployed backend size: `391,304,472` bytes (`373.2 MiB`)
- Deployed backend SHA-256:
  `dd24fa3643886b2e29eb31b8e906e0a45f1f8dc27b9c970719c70ee79e1e156f`
- Build profile: optimized Rust release with ThinLTO, line-table debugging in
  the Cargo artifact, and a separately stripped deployment copy
- Previous measured non-LTO release: 20 minutes 10.55 seconds to build,
  414,921,560 bytes unstripped, and 382,006,128 bytes stripped
- App bundle identifier: `com.sudhir.codex`
- Installed app: `/Applications/Sudhir-Codex.app`
- Frontend at the 2026-07-25 validation: `26.721.41059` / `5848`
- Frontend signing authority: `Alamelu Pi Local Code Signing`
- Unified Prism icon master SHA-256:
  `2205c7fa17ece5f641c8dbb1754ef10c54479516b909f9ae6efe5bd71f50cd6f`
- Official bundle identifier: `com.openai.codex`
- Open-model source catalog: 72 models across 9 providers
- Merged GPT/open catalog at release validation: 80 models
- Picker-visible catalog at latest frontend validation: 52 models
- Default approval policy: `never`
- Default sandbox: `danger-full-access`
- Default reasoning effort: `high`
- Maximum simultaneous agents per session: 6
- Cursor SDK pin: `@cursor/sdk` `1.0.23`
- Cursor routes:
  `composer-2.5-{fast,slow}` and `composer-latest-{fast,slow}`
- Native Google routes added on 2026-08-14:
  `pi-google/gemini-3.7-flash` and
  `pi-google-vertex/gemini-3.7-flash`, using Pi `0.82.1` and its modern
  `@google/genai` transport with Gemini thought-signature replay

Catalog counts are a dated baseline, not permanent assertions. Providers and
official GPT catalogs change over time. The four Cursor routes were added on
2026-07-25, after the dated release catalog counts above.

For historical full-app identity comparisons, the read-only command was:

```bash
/Users/sudhirjha/.playground/sudhir-codex-app/scripts/frontend-status
```

The 2026-07-25 frontend update to `26.721.41059` / `5848` took 11.22 seconds
after rebasing the fail-closed model-draft patch and passed subscription GPT,
direct DeepSeek, picker, effort, isolation, signature, and process checks. That
is historical validation. Current small-launcher maintenance follows the September
procedure above; do not use these historical version comparisons to trigger a build.
