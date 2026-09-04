# Computer Use launcher upgrade audit

Owner scope: fix all three diagnosed launcher compatibility/recovery issues and the relevant documents. No unrelated model, runtime, server, or version work.

<!-- critic_backend: claude (opus), interactive bash/tmux per owner routing -->
<!-- critic_id: 40a154aa-536c-4bed-88e2-d745a01a6707; tmux: codex-cua-opus-20260904 -->

## Plan Audit Round 1

**Reviewer:** claude (opus)
**Date:** 2026-09-04
**Artifact:** `documents/plan-audit-implementation/computer_use_launcher_upgrade_plan.md` (complete file, including the 2026-09-04 owner review-discipline and upgrade-preservation sections and the concrete preservation implementation).
**Scope audited:** the complete owner scope — (1) changed/missing upstream Computer Use SKILL.md, (2) obsolete dependency on the upstream `## Bootstrap` format, (3) recovery searching the wrong folders, the document updates that prevent repeated version-specific workarounds, and the added requirement that subsequent ChatGPT / Sudhir-Codex / gateway upgrades do not clobber this repair.

### Evidence verified independently (read-only)

All of these were confirmed against local artifacts, not assumed from the plan:

- ChatGPT `26.901.22334` build `7746` (`Info.plist`); installed Computer Use plugin `1.0.1000926`.
- `_instruction_template` (`_frontend_transition_control.py:543-570`) resolves `working_state / "frontend-control-runtime" / "original-plugins"`. For primary-home use that is `~/.sudhir-codex/frontend-control-runtime`, which **does not exist**, while the real backups are at `~/.sudhir-codex/control/original-plugins`. Problem (3) reproduced exactly.
- Read-only reproduction through the real module: `_instruction_template(<primary>, ".codex-plugin/computer-use-node-repl.md")` succeeds; `_instruction_template(<primary>, "skills/computer-use/SKILL.md")` raises `Computer Use plugin has no recoverable instruction template for skills/computer-use/SKILL.md`. This is the launcher error.
- Six backups exist at `~/.sudhir-codex/control/original-plugins` (`1.0.1000717`, `761`, `790`, `816`, `854`, `919`); all six carry a `## Bootstrap` match in **both** `skills/computer-use/SKILL.md` and `.codex-plugin/computer-use-node-repl.md`. The plan's "six valid backup templates" claim is accurate.
- The app-bundled `.../plugins/computer-use/skills/computer-use/SKILL.md` is 101 lines, headings `# Computer Use`, `# Computer Use Confirmations Policy`, `## Scope`, `## Definitions`, `## Computer Use Confirmation Modes`, `## Computer Use Confirmation Hygiene` — no `## Bootstrap`, no `## API surface`. `_BOOTSTRAP.search()` returns `False` on it. The plan's refusal to synthesise a Bootstrap into it is correct.
- The installed cache still carries the full instruction document at `.../computer-use/1.0.1000926/.codex-plugin/computer-use-node-repl.md` (198 lines) **with** `## Bootstrap` and `## API surface`. For `1.0.1000919` the backed-up `SKILL.md` and `computer-use-node-repl.md` are byte-identical, so the plan's "one common instruction source" decision loses no fidelity today.
- Legacy transition state has `~/.sudhir-codex-chatgpt/frontend-control-runtime/original-plugins` and `original-plugin-1.0.1000633`; the legacy resolver root in the plan is correct.
- `ensure_primary` does terminate a conflicting official ChatGPT instance (`frontend_transition.py:394-409` via `_terminate_processes`). The plan's handling of that hazard is correct and should be kept.
- Preservation surfaces are real: `GATEWAY_TESTS` exists (`scripts/tests/sudhir_targeted_regressions.py:16`) and already pins seven `test_frontend_transition` tests; `.github/workflows/source-ci.yml:121-153` (`gateway-linux`) runs `python scripts/tests/sudhir_targeted_regressions.py gateway`; `scripts/install-sudhir-codex:199-202` installs the gateway with `--editable` and contains zero references to `control` or `skills` (verified `grep -c` = 0 for both), and the live venv holds `__editable__.sudhir_codex_gateway-0.1.0.pth`. The plan's claims in §51 are accurate.

The plan's diagnosis and repair direction are sound. The findings below are gaps and one incorrect causal model, not a rejection of the approach.

### Findings

**F1. The Evidence misreads a deliberate upstream packaging variant as breakage, and the plan keeps injecting a `SKILL.md` that upstream intentionally omits. [severity: high]**

The cache manifest `~/.sudhir-codex/plugins/cache/openai-bundled/computer-use/1.0.1000926/.codex-plugin/plugin.json` contains a key the app-bundle copy of the same manifest does **not** contain:

```json
"skills": "./skills/",
"bundledContentVariant": "node-repl"
```

The two manifests are otherwise identical (same `name`, same `version` `1.0.1000926`). The app bundle ships `skills/computer-use/SKILL.md`; the cache extracted under the `node-repl` variant ships an empty `skills/` directory and keeps `.codex-plugin/computer-use-node-repl.md` as the instruction document. The historical backups confirm the transition: for `1.0.1000717` through `1.0.1000919` the cache did ship a `SKILL.md`, and it was byte-identical to the node-repl document. Upstream did not lose or restructure the skill — it de-duplicated it and now selects content by variant.

Two consequences the plan does not handle:

1. *Failure scenario:* after this repair runs, `_install_plugin_assets` (`_frontend_transition_control.py:598-626`) writes all three `PLUGIN_FILES` into every cache root, so it will **create** `<cache>/1.0.1000926/skills/computer-use/SKILL.md`, a file this variant deliberately omits, inside a plugin whose manifest declares `"skills": "./skills/"`. The frontend therefore loads a second Computer Use skill whose Bootstrap points at `<plugin root>/scripts/computer-use-client.mjs`, competing with the harness-owned stable skill that `AGENTS.md:11-14` declares to be the sole authority ("plugin-cache skills are compatibility copies and are not the authority"). The pre-repair behaviour never did this, because upstream always shipped the file and we only overwrote it.
2. *Scope-relevant consequence:* because the plan records the cause as "its private cache has an empty skills directory" rather than "the node-repl variant no longer ships a skill", the next variant flip (for example a switch to the MCP-server variant, whose skill is the short policy-only document, or the parallel `unified-computer-use` plugin already present at `26.831.21537`) reproduces the same emergency. That is precisely the repeated version-specific workaround the owner's scope exists to prevent.

*Required in the plan (no code beyond what is already authorized):* correct the Evidence paragraph to state the `bundledContentVariant` mechanism and the de-duplication, and record an explicit decision on whether the harness continues to write `skills/computer-use/SKILL.md` into a `node-repl`-variant cache. **Flagged for owner decision:** stopping that write is a behaviour change; if the owner prefers to keep writing it, say so in the plan and the finding is closed on the record rather than by code.

**F2. The preservation gate does not demonstrably cover problem (3), the defect that actually regressed. [severity: high]**

§49 names the five tests that will be added to `GATEWAY_TESTS`, and that tuple plus `source-ci`'s `gateway-linux` job is the owner's chosen mechanism for "upgrades must not clobber these fixes". But the Verification list (line 28) enumerates nine regression cases, and "primary backup recovery under control/original-plugins" — the wrong-folder bug itself — has no unambiguous owner among the five. `test_primary_control_recovers_from_owned_templates` may be intended to cover the stable skill and `control/plugin-assets` only; `test_transition_template_recovery_uses_transition_backups` is explicitly the *legacy* root.

*Failure scenario:* a future upstream rebase or reconciliation restores `_instruction_template`'s `working_state / CONTROL_ROOT_NAME` root for primary-home use. `sudhir_targeted_regressions.py gateway` and `source-ci` both stay green because no gate test asserts that a primary-home resolve reads `<primary>/control/original-plugins`, and the owner rediscovers the identical launcher error at the next ChatGPT upgrade.

*Required:* add an explicit mapping in the plan from the five gate tests to the nine verification cases, and ensure the primary `control/original-plugins` recovery-root assertion is inside one of the five gate tests rather than only in a non-gate regression.

**F3. The gate tests must be Linux-safe and ruff-clean, and the plan does not say so. [severity: medium]**

`gateway-linux` is `runs-on: ubuntu-22.04` and its step runs `python -m compileall`, `verify_sudhir_fork_contracts.py lint`, `run_sudhir_fork_contracts.py`, `sudhir_targeted_regressions.py gateway`, **and `ruff check sudhir_codex/src sudhir_codex/tests`**. There is no `skipUnless`, `sys.platform`, or `darwin` guard anywhere in `scripts/tests/sudhir_targeted_regressions.py` or in `sudhir_codex/tests/test_frontend_transition.py` (verified by grep: zero matches in both).

*Failure scenario:* any of the five new gate tests reaches `provision_primary_control_runtime` unmocked, or touches `/usr/bin/codesign`, `/Applications/ChatGPT.app`, or a `0o700` macOS-only path. The test passes on the owner's Mac, then `gateway-linux` fails on every unrelated push, and the local `sudhir_targeted_regressions.py gateway` gate quoted in `documents/FRONTEND-UPDATE.md` fails too — turning the preservation mechanism into a broken gate that gets disabled. The existing precedent to follow is `test_primary_local_skill_uses_stable_wrapper_outside_plugin_cache` (`test_frontend_transition.py:566-593`), which is pure and calls only `_rewrite_primary_skill`.

*Required:* state in the plan that the five gate tests exercise `_instruction_template` / asset preparation directly (or with `codesign` and component verification mocked), depend on no macOS-only path, and pass `ruff check`. The Verification bullet at line 29 should name `ruff check sudhir_codex/src sudhir_codex/tests` alongside compileall.

**F4. Stable-skill-first priority freezes the instruction lineage permanently, and the recovery pool can no longer gain a newer original. [severity: medium]**

§15 makes the stable skill the highest-priority template. Combined with F1, `control/original-plugins` only ever gains a file when the cache still ships it (`_install_plugin_assets` backs up only `if destination.is_file()`), so under the `node-repl` variant the `SKILL.md` backup pool is frozen at `1.0.1000919` forever. Independently, `launch` re-syncs after the app server appears (`frontend_transition.py:~152-155`) but `launch_primary` ends at `subprocess.run(command, check=True)` with **no** post-launch re-sync, and `documents/FRONTEND-UPDATE.md:154-157` already records that the 1.5-second timer is not a durable boundary.

*Failure scenario:* OpenAI ships a materially different `## API surface` (it already did — the backed-up document went from 18946 bytes at `1.0.1000854` to 13511 bytes at `1.0.1000919`). The harness regenerates its own frozen text from the stable skill indefinitely and never surfaces the divergence, so the model is given API instructions that no longer match what the preserved IPC-2 client and the current frontend expect, and the owner debugs it as a fresh incident.

Pinning to the preserved IPC-2 lineage may well be the right call — it is consistent with §13. The gap is that the plan neither states the freeze as a decision nor gives the deliberate refresh path. *Required:* state it explicitly and record, in the runbook edits §50 already authorizes, how the owner intentionally re-adopts a newer upstream lineage. No new code is implied.

**F5. No idempotence regression for the newly self-referential template path. [severity: medium]**

§15 makes the stable skill its own highest-priority template, and `_rewrite_primary_skill` (`_frontend_transition_control.py:519-540`) rewrites it with `_BOOTSTRAP.subn(..., count=1)`, where `_BOOTSTRAP = ^## Bootstrap\n.*?(?=^## )` requires a following `## ` heading. Today this is idempotent (the current stable skill has `## Bootstrap` at line 14 and `## API surface` at line 28, so the replacement is consumed and re-emitted identically), but the plan introduces the self-reference and covers it only with the live "second sync for repeatability" at line 31 — not with a unit case.

*Failure scenario:* a later edit makes the replacement text end without a trailing heading boundary, or moves Bootstrap to the end of the document. `subn` then returns `count != 1` and raises `Computer Use skill has no recognised Bootstrap section` on a healthy machine, or the Bootstrap block accumulates across syncs. Nothing in the module or the gate catches it: there is currently **no test at all** that exercises `_instruction_template`, which is why this defect shipped in the first place.

*Required:* add one regression asserting that resolving-and-rewriting the stable skill twice yields byte-identical output. This is one test case inside the module the plan already creates — it adds no functionality.

**F6. Second sync poisons the "original" backup pool. [severity: low]**

On the first post-repair sync, `<cache>/1.0.1000926/skills/computer-use/SKILL.md` does not exist, so no backup is taken and the harness writes it. On the second sync `destination.is_file()` is now true and `backups / root.name / relative` does not exist, so `_install_plugin_assets:620-623` copies the **harness-written** file into `control/original-plugins/1.0.1000926/skills/computer-use/SKILL.md`, where it sorts highest of the seven versions. The plan's own verification explicitly runs "a second sync for repeatability", so this path is on the tested route. Content impact is small (a later rewrite replaces the Bootstrap and preserves the API/policy text), but the pool stops being a pool of upstream originals, which is the property problem (3) exists to protect. *Suggested:* skip the backup for a destination the harness itself created, or state on the record that the pool is accepted as "last known good", not "upstream original". **The code option is a behaviour change — owner decision.**

**F7. `runtime_status()` reports the wrong home in primary mode. [severity: low]**

`runtime_status` (`_frontend_transition_control.py:990-1004`) computes `pluginVersions` from `_plugin_roots(paths.transition_state)` unconditionally. In primary-home mode `ensure_primary` prints that value, so it reports the legacy transition cache (`~/.sudhir-codex-chatgpt/.../computer-use/1.0.1000717`) while the live primary cache is `1.0.1000926`. This is the same wrong-home class as problem (3) and will actively mislead the next diagnosis. It is a status/reporting path, not recovery, so **a fix may be scope expansion — flagged for owner decision** rather than demanded.

**F8. Two preservation details are unactionable as written. [severity: low]**

(a) §52 says "the older separate publication checkout is on an unrelated pre-transition branch … do not overwrite or release it", but does not name its path; `/Users/sudhirjha/playground/sudhir-codex` does not exist on this machine, so an implementer cannot honour or verify the instruction. Name the exact path (and, if useful, the branch) so the constraint is checkable.

(b) `scripts/tests/sudhir_fork_contracts.toml` holds 33 contracts and **none** has a `source` entry naming `_frontend_transition_control.py` or `frontend_transition.py`; there is no `fork[...]` patch label for this area (labels present: `state`, `launcher`, `web-search`, `catalog`, `reasoning`, `history-tools`, `model-continuity`, `oauth`, `agents`, `tui`, `packaging`, plus `fix(gateway)`). The monthly rebase gate in `documents/BACKEND-MONTHLY-UPGRADE.md` is therefore blind to this repair at the contract level. The owner has explicitly chosen `GATEWAY_TESTS` + the runbook text as the mechanism and excluded new frameworks, so **this is recorded as a note for owner decision, not a required change.**

**F9. `documents/SUDHIR-CODEX-SYSTEM-OVERVIEW.md` is outside the document list. [severity: low]**

The repo's own project contract names it as operational truth (verified 2026-08-20). Its §3 triage table row reads "Browser/Computer Use fails | Stable shim/control runtime or trust hash | Validate the shim, helper, socket and current hash | Editing plugin-cache copies first" — which does not include the instruction-template/recovery-root failure mode this repair introduces handling for — and its component-ownership table (lines 75-82) does not list `~/.sudhir-codex/control/original-plugins` as the primary recovery root. The owner enumerated only `BACKEND-MONTHLY-UPGRADE.md` and `SUDHIR-CODEX-REPAIR-RUNBOOK.md` in §50, so **adding this third document is an owner decision**; if declined, the finding is closed on the record.

### Notes for the planner (do not "fix" these)

These were checked and are correct as written; no change is wanted:

- Refusing to synthesise a `## Bootstrap` into the short app-bundled skill (§13) — verified that document carries no API surface.
- Using the full node-repl instruction document as the template source (§15) — verified byte-identical to the historical `SKILL.md` at `1.0.1000919`.
- The legacy-transition resolver root `<transition>/frontend-control-runtime` (§15) — verified to exist with both `original-plugins` and `original-plugin-1.0.1000633`.
- Retaining the legacy-transition installed-plugin requirement while relaxing it for primary (§17).
- The `ensure-primary` termination hazard and the deferred normal-launch check (line 33).
- §51's assertions about `scripts/install-sudhir-codex` — independently verified true.

### Verdict: REVISE

Two high and three medium findings. F1 (wrong causal model plus injection of a variant-omitted skill) and F2 (the preservation gate does not demonstrably cover the wrong-folder defect) both bear directly on owner scope items and on the requirement that upgrades not clobber this repair. F3, F4 and F5 are concrete gaps in the plan's own chosen verification and preservation mechanism. F1, F6, F7 and F9 each contain an element flagged for owner decision rather than demanded, per the scope rules; the planner should not implement those elements without approval.

---

## Response to Plan Audit Round 1

### Accepted changes

- F1 — Accepted. The plan now records the node-repl content variant, never synthesizes omitted cache instruction files, and renders all primary instructions from one stable source using the same stable wrapper path. Respecting the new plugin layout is directly within the owner's item (1) fix; no extra component or approval is needed.
- F2 — Accepted. Added an explicit mapping of every case to the five gateway-gate tests; the owned-template test directly covers primary control/original-plugins, including wrong-root priority.
- F3 — Accepted. Gate tests use temporary files and mocked component/signature checks; add Ruff for changed Python files. No real macOS service in source CI.
- F4 — Accepted. Pinning instructions to the existing IPC-2 runtime is now explicit; the docs give a deliberate owner-authorized refresh path. Current upstream docs do not define the unchanged pinned client API. No runtime refresh in this task.
- F5 — Accepted. Add byte-identical repeated-provision assertions to the existing planned gate cases, covering the self-referential stable source and cache replacement.
- F6 — Accepted as resolved by F1: omitted cache instruction files stay absent, so the second sync cannot back up a manufactured skill. No separate backup-system change.
- F8(a) — Accepted. Named the actual publication path and its branch.
- F9 — Accepted as a small directly relevant documentation pointer. The owner asked to fix relevant documents; the earlier file list was the builder's, not an owner restriction.

### Disputed or excluded items

- F7 — Disagree with including a status-reporting repair here. It is outside the three diagnosed launcher problems; no change will be made. Also ensure_primary returns status_primary, not runtime_status, so the claim that its output exposes those legacy plugin versions is not accurate.
- F8(b) — Recorded, no new contract framework/registration is added. This repair uses the existing GATEWAY_TESTS/source-ci gate, explicit candidate checks, and upgrade preservation instructions, as planned. General contract-register changes are outside this chosen repair.

Owner discipline repeated: Opus must be diligent and thorough from round 1; no new findings after round 3 except errors introduced by fixes; anything outside scope is rejected.

## Plan Audit Round 2

**Reviewer:** claude (opus)
**Date:** 2026-09-04
**Artifact:** the complete amended plan, including the Evidence correction, the amended Architecture paragraph, the Plan-audit clarifications, the regression-to-gate mapping, and the Packaging-evidence correction section.
**Scope audited:** the complete owner scope — the three diagnosed launcher problems, the relevant documents, and preservation against subsequent ChatGPT / Sudhir-Codex / gateway upgrades. Round 1 evidence is retained; only the packaging question was re-opened, at the owner's direction.

### Two corrections to Round 1

Both are stated because they change what the documents should say.

**C1. My Round 1 F1 causal claim was wrong.** I asserted that `bundledContentVariant: "node-repl"` explains the missing `skills/computer-use/SKILL.md`. It does not. Two independent read-only checks refute it:

- The legacy transition cache at plugin version `1.0.1000717` **already declares** `"bundledContentVariant": "node-repl"` *and* upstream shipped a `skills/computer-use/SKILL.md` at that version — the legacy backup `~/.sudhir-codex-chatgpt/frontend-control-runtime/original-plugins/1.0.1000717/skills/computer-use/SKILL.md` is upstream-original (`globalThis.sky = (await import("@oai/sky")).sky;`), as is the primary backup for the same version. The marker is longstanding, not new, and did not accompany an omission.
- I verified the owner's supplied byte ranges with `mmap` rather than a regex scan. At offset `3173832`: `Ha=[{destination:[\`skills\`,\`computer-use\`,\`SKILL.md\`],source:[\`.codex-plugin\`,\`computer-use-node-repl.md\`]}]`. The enclosing materializer `Ka(e)` sets `r = Ha` when `computerUseSkillVariant === "node-repl"`, then runs `Promise.all(r.map(({destination,source}) => copyFile(join(pluginRoot,...source), join(pluginRoot,...destination))))`, and only afterwards writes `{...manifest, bundledContentVariant: n}` at offset `3175221`. The node-repl variant **creates** `skills/computer-use/SKILL.md` from the full node-repl instructions; it does not remove it. That is also why the `1.0.1000717`–`1.0.1000919` backups hold a long `SKILL.md` byte-identical to the node-repl document.

The plan's corrected wording — observed absence and observed manifest value, actor of removal unproven, repair keyed to file presence rather than to a variant label or version — is accurate and is the right basis. **F1 is resolved.** The architectural decision it produced (never manufacture an absent cache instruction document) survives the correction independently: whatever removed the file, re-creating it means writing a document the frontend did not materialize.

**C2. My Round 1 F7 call-path claim was wrong.** `ensure_primary` calls `status_primary` (`frontend_transition.py:322`), not `runtime_status`; `runtime_status` is reached only at `:156` (the `launch_primary` readiness gate) and `:480` (the transition-mode `status`). The planner's correction is right. F7 is excluded by the owner and is not re-raised.

### Round 1 findings — status

F1 resolved (see C1). F2 resolved: the mapping table assigns all nine Verification cases to the five gate tests, and `test_primary_control_recovers_from_owned_templates` explicitly asserts `control/original-plugins/<version>` recovery and that the wrong `frontend-control-runtime` root must not win. F3 resolved by the amended Verification bullet; I additionally confirmed the mechanism is sound, because `unittest.defaultTestLoader.loadTestsFromNames` raises a `_FailedTest` for an unknown name (verified: `successful: False, errors: 1`), so a future rename breaks `source-ci` loudly rather than silently dropping the gate. F4 resolved by the explicit IPC-2 pin plus the owner-authorized refresh path. F5 resolved by the byte-identical repeated-provision assertions. F8(a) resolved and verified: `/Users/sudhirjha/.playground/codex-with-openmodels-publication` exists on `fix/model-switch-compaction-history-tools-1e85ca` with its own tracked changes. F8(b) recorded as the owner directed. F9 resolved.

F6 is **not** fully resolved; see N1.

### Findings

**N1. The plan's "never records a harness-created skill as an upstream original" guarantee is refuted by the app's own materializer. [severity: medium]**

The amended Architecture paragraph justifies the file-presence rule by saying it avoids "recording a harness-created skill as an upstream original on the next sync". That guarantee assumed the harness is the only writer of `<cache>/<version>/skills/computer-use/SKILL.md`. The `Ka` evidence above shows the frontend writes it too, by copying `<cache>/<version>/.codex-plugin/computer-use-node-repl.md` — the exact file this repair patches.

*Failure scenario:* `launch_primary` runs `sync_primary_control_runtime` **before** `open` (`frontend_transition.py:166`), so the harness patches the cache node-repl document first. The app then runs `Ka` for that plugin root and copies the patched bytes to `skills/computer-use/SKILL.md`. On the next sync, `_install_plugin_assets:620-623` sees `destination.is_file()` true and `backups/1.0.1000926/skills/computer-use/SKILL.md` absent, and copies that app-materialized, harness-derived content into `control/original-plugins/` as an "original". The pool entry that sorts highest is then not upstream content, and the plan's stated guarantee is false. Functional impact is contained — a later `_rewrite_*` replaces the Bootstrap wholesale and preserves the API/policy text — but the plan asserts a correctness property it does not have, and a wrong stated property is the mechanism by which the next incident gets misdiagnosed, which is the owner's explicit anti-goal.

*Two acceptable resolutions, both in scope, either is sufficient:* (a) correct the wording — state that the cache skill may be app-materialized from the patched instruction document, and that `control/original-plugins` is a "last known good" pool rather than a guaranteed-upstream one; or (b) keep the guarantee and skip the backup when the destination's bytes match the instruction document the harness just wrote. (a) costs nothing and is the recommended option; (b) is a behaviour change and would need owner approval.

**N2. The `## Bootstrap` format dependency is only half removed, and the remaining half is unstated. [severity: low]**

Owner scope item (2) is the obsolete dependency on the upstream Bootstrap format. The plan removes the dependency on the section being *present in a particular file*, but `_BOOTSTRAP = re.compile(r"(?ms)^## Bootstrap\n.*?(?=^## )")` (`_frontend_transition_control.py:32`) additionally requires a **following `## ` sibling heading**. A full, valid instruction document whose `## Bootstrap` is the final section fails `_BOOTSTRAP.search()`, is rejected as a template by `_instruction_template`, and — if it were the only source — produces the "no recoverable instruction template" error on a document that plainly has a Bootstrap. Every document on this machine currently has `## API surface` after Bootstrap, so nothing fails today.

I am **not** asking for a regex change; that would be speculative hardening. The ask is one sentence recording the accepted limit, so a future occurrence is recognised immediately instead of being rediagnosed as the same missing-template bug.

**N3. The cache wrapper carve-out is ambiguous and, under the new bytes, writes a file nothing reads. [severity: low]**

The Architecture paragraph forbids creating a missing instruction document, then says "The wrapper can remain a compatibility asset" without saying whether that means still writing `<cache>/<version>/scripts/computer-use-client.mjs`. The current primary cache `1.0.1000926` has no `scripts/` directory at all (contents: `.mcp.json`, `.codex-plugin/`, `assets/`, `bin/`, empty `skills/`), while the legacy backup proves upstream did ship `scripts/computer-use-client.mjs` at `1.0.1000717` — so writing it now creates a file this cache does not otherwise contain, i.e. the same category of act the paragraph just prohibited. And because the same paragraph makes all primary instruction bytes carry the absolute stable wrapper path, nothing on the primary path references `<plugin root>/scripts/computer-use-client.mjs` any more. Say explicitly which of the two the implementer should do.

**N4. "Common instruction source" (Files §2) and the primary-scoped identical-bytes rule can be read as unifying the legacy transition too. [severity: low]**

The Architecture rule about one rendered Bootstrap and the stable wrapper path is scoped to *primary* provisioning, but the build-order line says "common instruction source" without that qualifier.

*Failure scenario:* an implementer unifies both paths and the legacy transition's cache documents receive the absolute `~/.sudhir-codex/control/computer-use-client.mjs` path. The legacy transition config sets `NODE_REPL_TRUSTED_CODE_PATHS` to `{transition_state}:{official_node_modules}` (`_frontend_transition_control.py:816`), which does not include the primary control root, so the legacy transition's Computer Use bootstrap is rejected as untrusted code. One clarifying sentence — the legacy transition keeps its `<plugin root>` form and its own wrapper — closes it.

### Notes for the planner (do not "fix" these)

Verified correct and deliberately unchanged: the file-presence rule being independent of variant labels and versions; the IPC-2 instruction pin plus the documented refresh path; the five-test mapping and its `GATEWAY_TESTS` / `gateway-linux` mechanism; the named publication checkout and branch; the decision to leave `runtime_status` and the 33-contract register untouched; `scripts/install-sudhir-codex` being benign for `control/` and `skills/`; the `ensure-primary` termination hazard handling.

### Verdict: REVISE

One medium (N1) and three low (N2, N3, N4). No high findings remain: the three diagnosed problems, the document set, and the upgrade-preservation mechanism are all now adequately specified and independently verified. N1 is the only blocker — it is an error introduced by the F1 fix, and the recommended resolution is a wording correction that requires no code and no scope change. N2, N3 and N4 are each one clarifying sentence in the plan.

---

## Response to Plan Audit Round 2

### Accepted changes

- N1 — Accepted: corrected the guarantee, without a new behaviour change. `original-plugins` is last-known-good recovery; the frontend may materialize harness-derived bytes and those may be backed up.
- N2 — Accepted: recorded the existing Bootstrap-followed-by-heading grammar as a compatibility limit; no speculative regex change.
- N3 — Accepted: explicitly retain the shared installer’s cache wrapper copy as compatibility behaviour. Only absent instruction documents are skipped.
- N4 — Accepted: explicitly scoped the common rendered source to primary. The legacy transition keeps its own plugin-root Bootstrap and wrapper.

Owner discipline repeated: Opus must be diligent and thorough from round 1; no new findings after round 3 except errors introduced by fixes; anything outside scope is rejected.

## Plan Audit Round 3

**Reviewer:** claude (opus)
**Date:** 2026-09-04
**Artifact:** the complete plan as of this round, re-read end to end — Owner scope, Evidence, Architecture and decisions, Files and build order, Verification, Debate and rollout, both owner sections, Concrete preservation implementation, Plan-audit clarifications, the regression-to-gate mapping, the Packaging-evidence correction, and the Accepted compatibility limit.
**Scope audited:** the complete owner scope, re-audited from scratch rather than as a delta — the three diagnosed launcher failures, the relevant existing documents, and preservation against subsequent ChatGPT, Sudhir-Codex and gateway upgrades. This is the last round in which pre-existing in-scope issues may be raised, so the sweep below was run to exhaustion rather than stopped at the first clean result.

### Round 2 findings — status

All four are applied and verified in the plan text. N1: the Architecture paragraph now states that the frontend may materialize a skill from the patched node-repl document and that `original-plugins` is a last-known-good pool, not a guarantee of upstream-original bytes. N2: the Accepted compatibility limit records the `## Bootstrap`-followed-by-a-level-two-heading grammar, with no speculative regex change. N3: the plan now decides explicitly to keep writing the cache `scripts/computer-use-client.mjs` compatibility wrapper via the shared installer while primary instructions use the stable wrapper. N4: Files item 2 now says the legacy transition retains its own plugin-root Bootstrap and wrapper and must not receive the primary absolute wrapper path. None is re-raised.

### Checked this round and found clean

Each of these was a candidate finding that I discharged with local read-only evidence rather than asserting.

- **Control-root wiring for problem (3).** `TransitionPaths.control_runtime` (`_frontend_transition_state.py:147-153`) passes both `transition_state=self.state` and `primary_state=self.primary_state`, so the explicit-control-root design in the plan has the roots it needs at every call site. No hidden second wrong root.
- **Gate-name resolution.** `sudhir_codex/tests/test_frontend_transition.py` contains exactly one `unittest.TestCase` subclass, `FrontendTransitionTests` (`:35`), so the five `test_frontend_transition.FrontendTransitionTests.<name>` gate strings are unambiguous. The CLI verb the signed launcher uses, `ensure-primary`, exists (`frontend_transition.py:548`), so the repair is on the live path.
- **Documented control-asset hashes.** I recomputed both pins in `documents/FRONTEND-UPDATE.md` against the live files. `~/.sudhir-codex/control/computer-use-client.mjs` is `f92046877983b72c9b1f8dbafcb87640b6adb6482563a0c0db2b02a027414a6b` and `~/.sudhir-codex/control/bin/sudhir-primary-node-repl` is `8f1dc3cc46c79eab0fdf03174c744eefb7fce6bac9a56bfdb95987a93f59c018`; both match `FRONTEND-UPDATE.md:151` and `:171` exactly. The repair does not change wrapper or shim text, so the pins stay valid and need no refresh.
- **The second `_rewrite_*` guard.** Both rewriters raise if `import("@oai/sky")` survives the Bootstrap substitution, which would fail on any template carrying a second direct-import occurrence outside the Bootstrap block. I counted occurrences in every candidate template — the installed `1.0.1000926` node-repl document, the `1.0.1000717`, `1.0.1000854` and `1.0.1000919` backups, `control/plugin-assets`, and the live stable skill. Every one is exactly `1` or `0`. Not a live hazard; no change wanted.
- **Config keys a ChatGPT upgrade could drop.** `apply_primary_control_config` has six settings with `insert_after=None` that hard-fail with `Missing required setting`. All six are present in the live primary config today (presence checked without reading values). More importantly, `documents/SUDHIR-CODEX-REPAIR-RUNBOOK.md:719-722` already documents this exact recurrence class from build `6849`, names both error strings (`Cannot insert`, `Missing required setting`), and instructs recording the version/build and asking the owner before changing the launcher. The class is already covered; I am not raising it as a finding. See L2 for the one narrow wording point.
- **Executability of the live verification step.** The plan's real-`sky.list_apps()` check depends on the sibling checkout. `~/.playground/sudhir-codex-app`, `tests/control-components.integration.test.mjs`, `scripts/verify-control-components`, and the named `dist/control-components-test-20260807-1` build directory all exist, so Verification item 5 is runnable as documented.
- **No existing gate test is invalidated.** The four config/shim/wrapper entries in `GATEWAY_TESTS` and the two direct rewrite tests (`test_primary_local_skill_uses_stable_wrapper_outside_plugin_cache:566`, `test_instruction_rewrite_preserves_policy:698`) all call the rewriters or config functions directly, or mock `sync_primary_control_runtime`. The planned changes leave every one of them passing, and `_rewrite_instructions` survives as the legacy-path renderer.
- **Preservation reaches the files that live outside the parity root.** `scripts/tests/sudhir_targeted_regressions.py` and `sudhir_codex/tests/test_frontend_transition.py` are not under `sudhir_codex/src/sudhir_codex_gateway/`, so gateway deployment parity alone would not protect them. The plan's "module + regressions + test-gate membership are a single required preservation unit" wording in the `BACKEND-MONTHLY-UPGRADE.md` edit closes that gap explicitly.

### Findings

**L1. `documents/FRONTEND-UPDATE.md` asserts stale version facts as current, and the plan's edit instruction does not clearly cover them. [severity: low]**

Line 143 states "The current bundled Computer Use plugin is `1.0.1000717`" — the installed plugin is `1.0.1000926`. Lines 145 and 154 describe the official client as `26.810.52044`; the installed app is `26.901.22334` build `7746`. These are asserted as present-tense fact, not as dated history, so Files item 3's instruction to "clearly mark old clone/repackage/version guidance as historical" does not obviously reach them. Since this document is already being edited and the owner's stated goal is to stop repeating version-specific workarounds, add one clause: stale present-tense version assertions are either corrected or explicitly dated. (`SUDHIR-CODEX-SYSTEM-OVERVIEW.md:84` and `:273` are already framed as "at the last verification" and need no change.)

**L2. The runbook's anchor sentence is narrower than the code it describes. [severity: low]**

`SUDHIR-CODEX-REPAIR-RUNBOOK.md:719-722` warns only about a later build removing "the `NODE_REPL_TRUSTED_CODE_PATHS` anchor". In `apply_primary_control_config` six settings carry `insert_after=None` and hard-fail identically: `mcp_servers.node_repl.command`, `NODE_REPL_NODE_MODULE_DIRS`, `NODE_REPL_NODE_PATH`, `NODE_REPL_TRUSTED_CODE_PATHS`, `BROWSER_USE_CODEX_APP_VERSION`, and `shell_environment_policy.set.NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S`. The runbook is already being edited under the preservation work; widening that one sentence to "any of the required keys" costs nothing and prevents the next occurrence being rediagnosed from scratch. **No code change is being asked for** — the existing "ask the owner before changing the launcher" protocol is correct and should stay.

**L3. `documents/bugs/BUGLIST.md` already has a preservation-section convention the plan does not reference. [severity: low]**

`SC-BUG-2026-08-08-001` carries a dedicated `### Upgrade preservation requirement` heading (`BUGLIST.md:111`). The plan routes preservation text to `SUDHIR-CODEX-REPAIR-RUNBOOK.md` and `BACKEND-MONTHLY-UPGRADE.md` and separately lists `BUGLIST.md` among the documents to update, without saying the new entry follows the existing structure. Say that it does, so the preservation requirement for this repair lands where the repo's own convention already puts it.

### Verdict: PASS

Zero high, zero medium, three low. The three diagnosed launcher failures are each addressed by a mechanism keyed to observed file presence rather than to a version or a variant label; the recovery roots are explicit and the wrong-root case is asserted in a gate test; the `## Bootstrap` dependency is reduced to a recorded, deliberate grammar limit; the document set now covers `FRONTEND-UPDATE.md`, `README.md`, `BUGLIST.md`, `SUDHIR-CODEX-REPAIR-RUNBOOK.md`, `BACKEND-MONTHLY-UPGRADE.md` and `SUDHIR-CODEX-SYSTEM-OVERVIEW.md`; and preservation against ChatGPT, Sudhir-Codex and gateway upgrades rests on mechanisms I verified exist and fail loudly. L1, L2 and L3 are each a single clause inside edits the plan already authorizes and do not block implementation.

Two standing caveats for the implementation review, carried forward rather than raised as findings: the plan's correctness now depends on the five gate tests genuinely asserting what the mapping table claims, and on them running clean on Linux under `ruff`. Both are implementation facts I will verify against the actual diff rather than against the plan.

## Response to Plan Audit Round 3

- L1 — Accepted: stale present-tense version claims will be corrected or explicitly historical.
- L2 — Accepted: widen the existing required-key warning without changing config behavior.
- L3 — Accepted: use the existing Upgrade preservation requirement heading for this bug.

Plan PASS accepted; implementing the owner-authorized fix. Discovery closed after overall round 3; later review covers errors introduced by fixes.

## Implementation Audit Round 1

**Reviewer:** claude (opus)
**Date:** 2026-09-04
**Artifacts:** the complete implementation record, the working-tree diff for `_frontend_transition_control.py`, `test_frontend_transition.py` and `sudhir_targeted_regressions.py`, all six document changes, the evidence directory `dist/diagnostics/computer-use-launcher-20260904T104455`, and the live primary runtime state.
**Scope audited:** the complete owner scope. Discovery of pre-existing issues closed at Round 3; this round looks only for errors in these fixes. I re-ran the meaningful checks myself rather than accepting the implementation record's claims.

### Independently re-executed, not accepted on report

- **The original defect is gone against real state.** The exact call that raised `Computer Use plugin has no recoverable instruction template for skills/computer-use/SKILL.md` in Round 1 now resolves for both relatives to `~/.sudhir-codex/skills/computer-use/SKILL.md`, and `~/.sudhir-codex/frontend-control-runtime` is still correctly absent and no longer consulted for primary.
- **The five gate tests.** Loaded through the real loader path and run: 5 tests, `OK`, 0 skipped, 0.120 s. The documented `PYTHONPATH=... .venv/bin/python -m unittest -v <five names>` command in `FRONTEND-UPDATE.md` also runs verbatim: 5 tests, `OK`. Full module: 27 tests `OK` (baseline was 22, so exactly five added). `ruff check` on all three changed files: `All checks passed!`. `git diff --check`: exit 0.
- **CI gate integrity.** All 44 `GATEWAY_TESTS` names resolve through `unittest`'s loader with no `_FailedTest` and no duplicates, and all five repair names are present — so `gateway-linux` will load and run the lane rather than fail or silently drop it.
- **Linux safety.** The five tests use `tempfile.TemporaryDirectory` fixtures via `_primary_control_case`, and mock only `_verify_control_components` and `_verify_openai_node_repl` — the sole `codesign` callers on this path. The `/Applications/...` strings in the module are config-fixture text, negative assertions, and a fake `ps` row; none touches that filesystem. My Round 3 caveat is discharged.
- **Live runtime state matches every claim.** The cache `skills/` directory is still empty, so `absentCacheInstructionsNotCreated` is true in fact. The stable skill, both `control/plugin-assets` documents, and the cached `.codex-plugin/computer-use-node-repl.md` are all byte-identical at `805270c57f3447d6e19e9dc4e5cc525816433e815a4f260af1ca4367bd32e9ec` — one instruction lineage, as designed. `control/original-plugins/1.0.1000926/` contains only the node-repl document and the wrapper; no manufactured `SKILL.md` was recorded as an original. Both pre-repair `SKILL.md` files carry same-directory `.before-launcher-repair-20260904T054933Z` backups.
- **Evidence integrity.** `accepted-source-hashes.json` matches the current bytes of the control module and the test module exactly. `red-tests.log` really does show `FAILED (failures=2, errors=11)` on the old implementation, `green-tests.log` and `gate-tests.log` show `OK`, and `live-smoke.log` shows the named real-`sky.list_apps()` test passing with `skipped 0`.
- **Documented commands and links.** `TransitionPaths.from_env` exists; there is genuinely no `sync-primary` CLI verb, as the document states; the signed launcher binary really does reference `gpt-pro-frontend-transition` and `ensure-primary`. All six cross-document anchors resolve. *(My first anchor check reported two broken links; the checker was wrong — it collapsed whitespace runs, which GitHub's slugger does not. Re-run with the correct rule, all six are `OK`.)*
- **Legacy transition invariants.** `_prepare_plugin_assets` still requires an installed plugin, so the plan's "legacy retains its installed-plugin requirement" holds even though the shared `_install_plugin_assets` guard was removed. No `<transition>/skills/computer-use/SKILL.md` exists, and legacy resolution lands on its own `frontend-control-runtime/plugin-assets`. `test_transition_template_recovery_uses_transition_backups` asserts the legacy output keeps `<plugin root>/scripts/computer-use-client.mjs` and does **not** contain the primary wrapper path, which is exactly the N4 hazard, now covered by a gate test.
- **Test-to-case coverage.** I checked each of the nine Verification cases against the five tests rather than against the mapping table: no-cache, missing skill and short skill (T1 subtests); cache replacement and pruning plus node-repl cross-document recovery (T3); primary `control/original-plugins/<version>`, `control/plugin-assets` and `control/original-plugin-*` with a `WRONG-ROOT` decoy planted under `frontend-control-runtime` (T2, six subtests); legacy backup roots (T4); genuine missing-source error (T5); API/policy markers and the stable wrapper path plus asset equality (`_assert_primary_instructions`); repeated provisioning byte-identity (T1, T3). All nine are genuinely asserted.
- **Task-only separation.** `runner-task-only.patch` is a single hunk containing exactly the five names and nothing else, so the six unrelated `GATEWAY_TESTS` additions from other in-flight tasks are demonstrably separable. `backend-runbook.patch` is 38 added lines in one new section with no deletions, consistent with the owner's instruction to keep that private runbook in place rather than force-add it. `documents/README.md` removals are confined to the obsolete clone/repackage guidance, a stale Electron profile path and a stale "current installed frontend" heading — corrections the plan directed — and the before-copy is retained under `before/documents/`.

### Findings

None. No high, medium or low findings.

### Observations recorded, no action requested

Both are on the record now because discovery closes after this round, not because either needs fixing.

- The second sync copied the harness's own cache wrapper into `control/original-plugins/1.0.1000926/scripts/computer-use-client.mjs` as an "original". It is inert: the wrapper is never a template candidate (`relatives` spans only `PLUGIN_FILES[1:]`), and `_install_plugin_assets` always rewrites the cache wrapper from `assets.wrapper` rather than from the backup. The documents already carry the correct caveat that these pools are last-known-good rather than guaranteed upstream originals, so no remediation is wanted.
- `test_primary_control_reports_missing_instruction_sources` passes on the old implementation as well as the new one — it guards the error path rather than proving a behaviour change. That matches its assigned case and the implementation record does not claim otherwise; the other four are genuinely red-before.

### Outstanding sequencing, not defects

The focused commit is pending by design, after this review; `runner-task-only.patch` and `accepted-source-hashes.json` are the evidence that the split is exact, and the staged diff is the thing to confirm at commit time. Normal GUI relaunch acceptance remains deferred under the owner's approval requirement, and the implementation record states plainly that no post-restart GUI task was tested. That residual is small and correctly characterised: all changed logic is inside primary provisioning, which was exercised directly against real installed resources and signed components twice, plus a real signed-shim `sky.list_apps()`; the untested remainder is the unchanged process termination and relaunch path.

### Verdict: PASS

Zero high, zero medium, zero low. The three diagnosed failures are fixed in code, covered by five tests that assert what the mapping table promised, and verified against the live system; the six documents are consistent, accurate and internally linked; and the preservation unit — module, tests and `GATEWAY_TESTS` membership — is recorded in the upgrade runbooks and demonstrably loads in the existing `gateway-linux` lane. I remain available as the same critic for any review of errors in these fixes.

---

## Response to Implementation Audit Round 1

Accepted PASS: zero findings. No new findings were accepted after overall round 3. The reviewer’s observation wording “discovery closes after this round” is corrected here: discovery closed at Plan Audit Round 3, as her scope paragraph also states. The observations require no changes and describe already agreed behavior.

The same Opus critic served all four reviews and the dedicated tmux session was closed after PASS. Heartbeat logs and final pane were preserved in the evidence directory. Proceeding with the scoped commit; normal GUI relaunch remains the explicit safe-handoff item.

## Rollout record

Source repair committed as `0630f079e1261cfc67b4f1da67f17d395364e3a9`. Exactly the control module, its test module and five runner entries were staged. Committed module/test bytes match the reviewed hashes and the already verified local editable installation. Unrelated tracked changes and untracked files are preserved. Documentation records the remaining normal-GUI-restart handoff. No push/release or server deployment performed.
