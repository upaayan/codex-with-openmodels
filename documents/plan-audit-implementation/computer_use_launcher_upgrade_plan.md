# Computer Use launcher upgrade repair

## Owner scope and authorization

On 2026-09-04 the owner asked to fix the diagnosed launcher failure using the debate-loop, fix the relevant documents, properly address (1) missing/restructured upstream Computer Use skill and (2) the obsolete upstream Bootstrap assumption, and make (3) recovery refer to the correct folders. The request explicitly authorizes implementation and local operational verification after review. Do not broaden into model routing, Rust, other frontends, server deployment, or unrelated changes. No version bump, new app bundle, or new runtime copy is needed.

## Evidence

ChatGPT 26.901.22334 build 7746 installed Computer Use 1.0.1000926. Its private cache declares `bundledContentVariant: node-repl` and currently lacks SKILL.md (empty skills directory); the app-bundled short skill lacks `## Bootstrap`. The full node-repl instruction file still has Bootstrap and API instructions. The current stable skill at `~/.sudhir-codex/skills/computer-use/SKILL.md` is intact and uses the preserved IPC-2 runtime. Six valid backup templates exist at `~/.sudhir-codex/control/original-plugins`. `_instruction_template` incorrectly searches `frontend-control-runtime/original-plugins` for primary-home use. Read-only execution reproduced the exact launcher error.

## Architecture and decisions

Keep the existing harness-owned stable skill, stable wrapper, signed shim, and preserved IPC-2 runtime. Upstream plugin caches are compatibility destinations, never the primary source of truth. Do not add a Bootstrap to a short upstream skill: that would omit the API instructions for the preserved client.

The template resolver will accept an explicit control root: primary uses `<primary>/control`; the legacy transition uses `<transition>/frontend-control-runtime`. Prefer the stable skill and matching preserved plugin-assets, then valid templates in the selected control root's original-plugins and legacy original-plugin-* backups. Compatible installed plugin instructions are the last recovery source. A full node-repl instruction document can supply the template when a plugin skill is missing or no longer contains Bootstrap. Only a full compatible template is rewritten; preserve its API and policy text. If every compatible source is absent, retain a clear error instead of producing incomplete instructions.

Primary provisioning will resolve one compatible instruction template and render the stable-wrapper Bootstrap once. The stable skill and both preserved plugin-assets instruction documents use those identical bytes and the same stable wrapper path. Cache installation updates only instruction documents already present in that cache variant; it must not create a missing SKILL.md or node-repl instruction document. Continue writing the cache scripts/computer-use-client.mjs compatibility wrapper, even when absent; this preserves the existing wrapper installer shared with the legacy transition. Primary instructions use the stable wrapper instead. The file-presence rule avoids creating absent instruction documents ourselves. The frontend may later materialize a skill from our patched node-repl document, so original-plugins is a last-known-good recovery pool, not a guarantee of upstream-original bytes. The file-presence rule does not depend on variant labels or a specific version. Primary launch will not require a plugin-cache directory: it can provision its stable skill/shim/wrapper from owned assets or backups and report an empty compatibility-version list. Existing legacy-transition provisioning can retain its installed-plugin requirement. Recovery and installation use the same control root.

## Files and build order

1. Add focused regressions in `sudhir_codex/tests/test_frontend_transition.py` and show failures with the old code.
2. Repair `sudhir_codex/src/sudhir_codex_gateway/_frontend_transition_control.py`: template priority, explicit recovery roots, one primary instruction source, optional primary cache copies. The legacy transition retains its own plugin-root Bootstrap and wrapper; do not give it the primary absolute wrapper path. Keep launcher/process and model code unchanged.
3. Update the existing `documents/FRONTEND-UPDATE.md`, `documents/README.md`, and `documents/bugs/BUGLIST.md` in place: current signed launcher -> official ChatGPT -> private backend architecture; stable paths; correct recovery roots; precise verification commands. Clearly mark old clone/repackage/version guidance as historical rather than current. Preserve historical evidence.
4. Keep this single plan/audit/implementation document set, accumulating rounds without new version documents.

## Verification

- Regression cases: primary stable skill with no installed cache; a cache with missing SKILL.md; Bootstrap-free upstream skill; replacement/pruning of cache versions between two syncs; primary backup recovery under control/original-plugins; legacy transition backup recovery under frontend-control-runtime/original-plugins (and original-plugin-*); full node-repl instructions as recovery when the short/missing skill cannot supply the legacy API; genuine missing-template error; preserved API/policy and stable wrapper paths.
- Run only the frontend transition test module (plus any exact existing macOS control test needed for the live smoke), syntax compilation, Ruff on the changed Python files, and whitespace checks. The five gate tests use temporary trees, mock codesign/component verification where provisioning is exercised, and do not depend on installed macOS paths or live services, so the existing Linux source-CI lane can run them. No broad upstream/Rust suite.
- Before changing an existing runtime SKILL.md, create a timestamped backup in its same directory and retain it. Preserve current runtime/config preimages for the local sync.
- Verify using the installed private Python interpreter, which imports this checkout via its editable path. Run `sync_primary_control_runtime` to apply the repaired stable assets without terminating the active official app. Verify stable paths and exact cache state, then a second sync for repeatability.
- Run read-only real `sky.list_apps()` through the signed primary node-repl shim/stable wrapper with the existing macOS integration test. Record actual output.
- Review the signed launcher/ensure-primary path and current process state before GUI launch: ensure-primary terminates a conflicting official ChatGPT instance. If that includes this active task's host, do not terminate it; finish all safe verification first, then arrange a safe handoff with the owner for the one remaining normal-launch check.

## Debate and rollout

One Opus critic audits the full scope and complete artifacts each round; no scope expansion or speculative hardening. Reuse her across planning and implementation. Implement after plan PASS under the owner's explicit fix request. Require implementation PASS, commit only this repair's files (documents/README.md is already untracked; preserve its existing content), apply/verify the local launcher runtime, and record final commit/verification. This is a macOS launcher fix, so deployment is the local editable launcher runtime, not trading servers or a Rust build. Preserve unrelated tracked changes and untracked files.

## Owner review discipline (2026-09-04)

Opus is the critic. Be diligent and thorough from round 1. No new findings after round 3; errors introduced by fixes may still be raised. Anything outside scope will be rejected. Repeat this instruction in every round prompt.

## Owner upgrade-preservation requirement (2026-09-04)

The owner additionally requires that subsequent ChatGPT, Sudhir-Codex, and gateway upgrades do not clobber these fixes. This authorizes preservation requirements for this exact repair in the relevant upgrade runbooks and existing targeted regression/contract mechanisms. Verify the existing source/install/update paths that can overwrite this module or its stable skill, and add only the checks or preservation entries needed for this Computer Use repair. No unrelated upgrade or provider changes are authorized.

### Concrete preservation implementation

- Add the five focused tests to the existing `GATEWAY_TESTS` tuple in `scripts/tests/sudhir_targeted_regressions.py`: `test_primary_control_survives_plugin_layout_changes`, `test_primary_control_recovers_from_owned_templates`, `test_primary_control_recovers_from_node_instructions_and_cache_replacement`, `test_transition_template_recovery_uses_transition_backups`, `test_primary_control_reports_missing_instruction_sources`. Existing `.github/workflows/source-ci.yml` already executes that gateway lane; no new workflow or general upgrade framework is needed. Preserve unrelated existing changes in the runner and commit only this task's additions.
- Extend documentation edits to the existing `documents/SUDHIR-CODEX-REPAIR-RUNBOOK.md` (targeted known-error recovery and frontend-update checks) and `documents/BACKEND-MONTHLY-UPGRADE.md` (module + regressions + test-gate membership are a single required preservation unit before accepting a candidate or applying gateway reconciliation). Do not update unrelated procedures.
- `scripts/install-sudhir-codex` installs the gateway editable and leaves private skill/control directories alone. Gateway deployment parity already covers `sudhir_codex/src/sudhir_codex_gateway/`; its conflict-aware reconciliation must retain this repair. Document an explicit rejection of candidates that omit the repair or its tests, with the exact focused command to verify on the candidate source and after local activation. No installer code change is presently indicated.
- The older separate publication checkout `/Users/sudhirjha/.playground/codex-with-openmodels-publication` is on `fix/model-switch-compaction-history-tools-1e85ca` and has its own tracked changes. Do not overwrite or release it. Commit the focused repair in the current operational checkout, and require future publication/rebase work to carry this repair's code/tests/gate together. This task creates no release, tag, app build, or general repository reconciliation.

## Plan-audit clarifications

The instruction lineage is deliberately pinned to the preserved IPC-2 runtime; current OpenAI instructions must not silently replace it. Upstream instruction changes do not by themselves change the pinned client API. The runbooks will describe a deliberate owner-authorized runtime/instruction refresh: preserve the old stable skill and control assets, verify the proposed full instructions against the chosen runtime, then replace the stable skill at its same path and verify provisioning and real list_apps. No runtime/instruction refresh is part of this repair.

Regression-to-gate mapping (all five names appear in GATEWAY_TESTS):

| Gate test | Cases |
|---|---|
| test_primary_control_survives_plugin_layout_changes | Independent temporary primary homes for no cache, node-repl variant missing skill, and upstream short Bootstrap-free skill; stable wrapper/API/policy preserved; cache copies obey existing variant; two syncs produce identical stable bytes |
| test_primary_control_recovers_from_owned_templates | Stable skill absent: recover independently from control/plugin-assets (skill or full node instructions), control/original-plugins/<version>, and control/original-plugin-*; assert the exact recovered primary source and preserved marker; wrong frontend-control-runtime root must not win |
| test_primary_control_recovers_from_node_instructions_and_cache_replacement | No owned source: recover from full cached node-repl instructions while cache skill is absent/short; replace/prune cache version between syncs; stable content remains identical, no omitted skill created |
| test_transition_template_recovery_uses_transition_backups | Exact legacy transition frontend-control-runtime/original-plugins and original-plugin-* recovery; primary control path is not substituted |
| test_primary_control_reports_missing_instruction_sources | No compatible stable/asset/backup/cache template raises the clear instruction-source error |

`documents/SUDHIR-CODEX-SYSTEM-OVERVIEW.md` is also directly relevant to the owner's request to fix relevant documents: add a small triage/root pointer for this exact failure. The earlier document list was the builder's list, not an owner-imposed exclusion. No unrelated overview revisions. Leave runtime_status reporting and the 33-contract framework untouched: those are separate from the approved repair and chosen existing gateway gate.

### Packaging-evidence correction during Plan Audit Round 2

The manifest marker alone does not establish deliberate skill removal. Read-only app.asar evidence at byte offsets 3173832–3175221 defines the node-repl materialization copy from `.codex-plugin/computer-use-node-repl.md` to `skills/computer-use/SKILL.md`, then writes the variant marker. The actual primary cache lacks the skill; the exact later removal actor is unproven. Documentation must state the observed mismatch, not assert that the node-repl marker itself removes the skill. Recovery must work regardless of why an upstream/cache file is absent or changed. The diagnosis of the mandatory missing-template lookup and wrong primary backup root is unchanged.

### Accepted compatibility limit

The preserved IPC-2 templates must contain a `## Bootstrap` section followed by another level-two heading (currently `## API surface`). This repair removes reliance on the current upstream skill layout; it does not broaden the legacy template grammar. A deliberate future runtime/instruction refresh must verify this format.

### Plan Round 3 accepted documentation details

Correct or explicitly date stale present-tense frontend/plugin version assertions. In the existing config-anchor warning, refer to any required key rather than only trusted-code paths. Follow the BUGLIST convention with an explicit Upgrade preservation requirement heading for this repair.
