# Computer Use launcher upgrade repair — implementation

## Scope and outcome

Implemented the approved plan after Opus Plan Audit Round 3 PASS. The scope is
only the three diagnosed startup/template/recovery failures, relevant existing
documents, and preservation against ChatGPT/Sudhir-Codex/gateway upgrades.
The owner’s discovery cutoff is now closed: later reviews may identify errors
in these fixes, not new pre-existing findings. No version bump, new app bundle,
new runtime copy, Rust/model-routing change, server deployment or publication
checkout change occurred.

## Implementation

- `_frontend_transition_control.py`: explicit selected control root; owned stable
  skill/assets/backups before cache sources; cross-document recovery from full
  node-repl instructions; primary instructions rendered once with the stable
  wrapper; no primary cache requirement; only existing cache instruction files
  updated. The shared wrapper copy and legacy transition Bootstrap are retained.
- `test_frontend_transition.py`: five tests with isolated temporary primary trees,
  cache deletion/replacement, short/missing instructions, both owned document
  forms, correct/decoy primary and transition recovery roots, API/policy markers,
  repeatability, and genuine missing-source failure. Only signature/component
  verification is mocked. No macOS path or service is used by these five tests.
- `scripts/tests/sudhir_targeted_regressions.py`: five exact method names added to
  the existing `GATEWAY_TESTS`, already run by source-CI gateway-linux. Unrelated
  existing runner additions are preserved and excluded from this repair commit.
- Six existing docs updated in place: FRONTEND-UPDATE, documents/README, BUGLIST,
  BACKEND-MONTHLY-UPGRADE, SUDHIR-CODEX-REPAIR-RUNBOOK and SYSTEM-OVERVIEW. Current
  small-launcher instructions precede collapsed historical clone procedures;
  recovery roots, deliberate IPC-2 lineage, supported sync, tests, preservation
  unit and safe relaunch boundary are consistent. Round 3 L1–L3 applied.

The cause of the cache skill’s removal remains unproven. The app.asar materializer
can create it from node-repl instructions. No variant-label removal claim remains
in current docs. The legacy Bootstrap-followed-by-heading grammar is an explicit
accepted limit, not silently presented as universal upstream compatibility.

## Verification Round 1

All commands ran in `/Users/sudhirjha/.playground/sudhir-codex` unless stated.
Evidence directory:
`dist/diagnostics/computer-use-launcher-20260904T104455`.

1. Baseline old module: 22 existing tests PASS (`baseline-tests.log`).
2. Added regressions before repair: `red-tests.log` records 27 tests, 2 failures
   and 11 subcase errors with the old implementation. This reproduced mandatory
   cache/skill failure, unsupported explicit root and cache-first priority.
3. After repair: `.venv/bin/python -m unittest discover -s sudhir_codex/tests
   -p test_frontend_transition.py -v`: 27 tests PASS (`green-tests.log`).
4. Loaded `GATEWAY_TESTS` using `runpy.run_path`, required each of the five
   explicit expected names exactly once, and passed those exact names to
   unittest’s loader: 5 tests PASS, no skips (`gate-tests.log`). This did not
   run unrelated gateway/provider tests. Missing method names fail the loader.
5. `.venv/bin/python -m py_compile` on the control module, transition tests and
   targeted runner: exit 0 (`syntax.log`).
6. `ruff check` on those same three files: All checks passed (`ruff.log`). Used
   the existing `/opt/homebrew/Caskroom/miniconda/base/bin/ruff`; the private
   venv has no Ruff module and no dependency was installed.
7. `git diff --check`: exit 0 (`whitespace.log`).

### Local operational verification

Before sync, copied all existing managed primary config/control/cache outputs to
`runtime-before/`, recorded SHA-256 values in `runtime-before.json`, and made
same-directory timestamped backups of both existing runtime SKILL.md files.
The backups end in `.before-launcher-repair-20260904T054933Z`.

Called `sync_primary_control_runtime(TransitionPaths.from_env())` twice using
the installed `.venv/bin/python`. Its editable import resolves to the repaired
module in this operational checkout. Both calls succeeded against the real
installed ChatGPT resources and signed components. `runtime-verification.json`
records exact metadata and hashes:

- `secondSyncByteIdentical: true` for every managed output including config;
- `stableSkillUnchanged: true`, SHA-256
  `805270c57f3447d6e19e9dc4e5cc525816433e815a4f260af1ca4367bd32e9ec`;
- `absentCacheInstructionsNotCreated: true`;
- compatibility version `1.0.1000926`;
- both preserved instruction documents and the present cached node-repl
  document match the stable skill exactly.

Live smoke from `/Users/sudhirjha/.playground/sudhir-codex-app`:

```bash
SUDHIR_CONTROL_BUILD_DIR=dist/control-components-test-20260807-1 \
SUDHIR_CONTROL_TEST_CUA_LAUNCH=1 \
node --test --test-concurrency=1 \
  --test-name-pattern='stable primary Computer Use boundary ignores poisoned frontend environment' \
  tests/control-components.integration.test.mjs
```

`live-smoke.log`: 1 test PASS, 0 failed, 0 skipped, 1340 ms total. The actual test
asserts successful real `sky.list_apps()`, count > 0, Finder present, exactly one
configured helper, and socket mode 0600 despite poisoned environment settings.
The test closes its own client only; it does not quit the active ChatGPT app.

Normal GUI launcher/restart acceptance is pending a safe owner handoff: the
unchanged `ensure-primary` entrypoint terminates an official ChatGPT process,
which currently hosts this task. The repair runbook §0 requires owner approval
for that disruption. Startup provisioning and real Computer Use have been
verified without it; no claim is made that a post-restart GUI task was tested.

## Repository preservation and rollout

This is a local editable installation, so the repaired code is already used by
fresh launcher invocations; safe local provisioning is applied and verified.
Opus Implementation Audit Round 1 PASS with zero findings. Source repair commit:
`0630f079e1261cfc67b4f1da67f17d395364e3a9` (`0630f079e`). The committed module/test bytes match the
reviewed and live-verified source hashes. The dedicated critic session is closed.

The operational checkout had unrelated tracked changes and untracked files at
start. Stage only this repair. `documents/README.md` was already untracked; its
existing content is preserved. `documents/BACKEND-MONTHLY-UPGRADE.md` is actually
ignored (the initial plan inventory called it tracked in error); keep its
existing private status, update it in place, and retain its before-copy and
task-only delta in `backend-runbook.patch` rather than force-adding a
3000-line private runbook. The same preservation unit and required checks are
recorded in the tracked FRONTEND-UPDATE, BUGLIST and repair documents.
The older separate publication checkout has its own tracked changes and lacks
the transition files; no release/tag/push or reconciliation was requested here.
Future candidates must retain the committed source/tests/gate as one unit.

## Final handoff

Safe local rollout is complete: the editable launcher uses the committed repair,
two real syncs passed, and the live signed-shim test passed. Six relevant docs
are updated in place, with historical instructions clearly separated.
Only normal post-restart GUI acceptance remains: finish this task, then close
ChatGPT and reopen `/Applications/Sudhir-Codex.app`. The launcher may stop an
official instance, so do not trigger it from an active repair task without the
owner’s disruption approval. No remote push/release was performed.
