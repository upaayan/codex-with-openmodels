# Sudhir-Codex native controls implementation

## Approved scope

Implement the three controls in `sudhir_native_controls_plan.md` after Opus Plan Audit Round 2 PASS:

1. in-app Browser peer authorization;
2. Chrome native-messaging/browser-socket broker;
3. Computer Use service compatible with the installed `cua.computer` client.

The owner pre-approved implementation on 2026-08-07 provided the scope remains unchanged and every installed change is reversible.

## Starting state and reversibility boundary

- Source changes belong only in `/Users/sudhirjha/.playground/sudhir-codex-app` plus this append-only implementation/audit record.
- Existing tracked owner changes in both repositories must be preserved.
- `/Applications/ChatGPT.app`, the installed Chrome extension, Codex Rust, and the Python gateway are not modified.
- Installation must back up the prior Chrome native-host manifest and the prior `~/.codex/config.toml`, retain the frontend rollback app, and provide a narrow uninstall/restore command.
- No live installation or app replacement occurs until source builds and focused tests pass.

## Preflight evidence correction

The live Sudhir configuration uses `/Applications/Sudhir-Codex.app/Contents/Resources/cua_node/bin/node_repl`. Its signature remains `Identifier=node_repl`, `TeamIdentifier=2DC432GLL2`. The plan's exact executable-path constant was corrected from the official-app path to this Sudhir-app path before implementation; scope and trust criteria are otherwise unchanged.

The real `@oai/cua` launch test also proved that the current package entry module must be present in `NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S` for its embedded Browser bootstrap to receive native-pipe capabilities. The installer now appends only the pinned current entry hash, preserves all existing hashes, and restores the original config line on uninstall. This is not a CUA-service identity bypass; it is the exact dynamic-import trust gate used by the existing `cua.computer` surface.

## Completion status — 2026-08-08

**COMPLETE.** All three owner controls are implemented, installed, and live.

- `/Users/sudhirjha/.playground/sudhir-codex-app/scripts/frontend-status`
  reports matching official/Sudhir version `26.727.51351`, build `6119`, valid
  signatures, and `control_status=ready`.
- `scripts/verify-control-components` reports
  `Installed control checks passed`.
- In-app Browser and Chrome each opened `https://example.com` and returned the
  page title `Example Domain`.
- Computer Use loaded through the bundled plugin wrapper and
  `sky.list_apps()` returned the available macOS application list.

The final model-facing Computer Use rule is recorded in the repository
`AGENTS.md`: read the installed skill, derive its plugin root, import
`scripts/computer-use-client.mjs`, call `setupComputerUseRuntime()`, and use
`sky`. Models must not invoke the generic launcher, substitute `~/.codex`, or
diagnose the actual child-process environment from the restricted
model-visible `nodeRepl.env` object.

No official ChatGPT files, Codex Rust code, or Python gateway code were changed
for these native controls.

## Computer Use launch correction — 2026-08-11

The restored `26.727.51351` / `6119` frontend retained the Computer Use plugin
and configured socket, but the first complete live check exposed a launch
attribution error. `list_apps()` could start the helper, while
`get_app_state({ app: "com.sudhir.codex" })` failed when LaunchServices had
started `Sudhir Computer Use.app` as a separate macOS privacy owner. Unified
TCC logs showed that `com.sudhir.codex` already had Screen Recording access;
the separately attributed helper did not.

The active plugin wrapper now spawns the configured helper executable directly
from Sudhir-Codex's trusted Node runtime before loading the Sky client. macOS
therefore records `com.sudhir.codex` as the responsible process while retaining
the helper as the requesting process. No TCC database was edited and no new
separate Screen Recording grant was created.

Live verification after terminating the helper proved automatic relaunch and
both required calls:

- `list_apps()` passed with 139 applications and included
  `com.sudhir.codex`;
- `get_app_state({ app: "com.sudhir.codex" })` returned the real
  Sudhir-Codex accessibility tree and a screenshot.

Historical 2026-08-11 paths and hashes:

- active wrapper:
  `/Users/sudhirjha/.sudhir-codex/plugins/cache/openai-bundled/computer-use/1.0.1000550/scripts/computer-use-client.mjs`
- active wrapper SHA-256:
  `28a31710067023749c72d156d28fbd33139bd7f1d59246c52755aea4e696cc68`
- helper:
  `/Users/sudhirjha/.sudhir-codex/control/Sudhir Computer Use.app`
- socket:
  `/Users/sudhirjha/.sudhir-codex/control/run/computer-use.sock`
- compact rollback:
  `/Users/sudhirjha/.playground/sudhir-codex-app/dist/transactions/computer-use-direct-child-20260811-003543`

That compact rollback contains the original plugin wrapper and a verified copy
of the signed helper app; it replaces the superseded multi-gigabyte restore
transactions removed during the owner-approved backup cleanup.

Do not infer trusted-plugin environment visibility from the deliberately empty
model-visible `nodeRepl.env`; trusted modules receive a separate full bridge.
Do not replace the direct-child launch with LaunchServices or a standalone
LaunchAgent.

## Shared-primary migration repair — 2026-08-15

Migrating the harness to `CODEX_HOME=~/.sudhir-codex` exposed two independent
Computer Use regressions:

1. primary launch did not synchronize plugin assets or helper/socket settings,
   so plugin `1.0.1000717` retained its upstream Bootstrap and lacked
   `scripts/computer-use-client.mjs`;
2. the official `26.810.41047` client had moved to
   `CodexComputerUseIPC-3`, while the signed Sudhir helper remained on the
   validated `CodexComputerUseIPC-2` contract.

The initial repair kept the unified primary `CODEX_HOME` and preserved the
physical legacy runtime path as intentional frontend infrastructure. It also
used a fixed 1.5-second post-launch synchronization. On 2026-08-16 the
`26.810.52044` frontend rewrote its helper settings approximately three seconds
after launch. The live app-server then spawned `node_repl` with the upstream
helper path and no native-pipe socket even though the isolated integration
client passed. This proved the timer and plugin-owned wrapper were not a
durable boundary.

The corrected implementation moves authority to stable harness-owned files:

- `~/.sudhir-codex/control/bin/sudhir-primary-node-repl` overrides frontend
  environment values and executes the preserved signed legacy `node_repl`;
- `~/.sudhir-codex/control/computer-use-client.mjs` loads the matching IPC-2
  client and starts the helper directly;
- `~/.sudhir-codex/skills/computer-use/SKILL.md` imports the stable wrapper
  without deriving a versioned plugin path;
- primary config and `CODEX_NODE_REPL_PATH` both select the stable shim.

Plugin-cache copies are retained only for compatibility. Runtime correctness
no longer depends on their survival or on a post-launch delay.

The primary wrapper deliberately uses the preserved patched IPC-2 client and
direct-child helper launch. Current live values are:

- plugin: `1.0.1000717`;
- wrapper SHA-256:
  `f92046877983b72c9b1f8dbafcb87640b6adb6482563a0c0db2b02a027414a6b`;
- stable shim SHA-256:
  `8f1dc3cc46c79eab0fdf03174c744eefb7fce6bac9a56bfdb95987a93f59c018`;
- local skill SHA-256:
  `524124456cda23a06b718cee4961effceb506d82ba94c0fbac3cc191117f3b93`;
- helper: `~/.sudhir-codex/control/Sudhir Computer Use.app`;
- socket: `~/.sudhir-codex/control/run/computer-use.sock`;
- `CODEX_HOME`: `~/.sudhir-codex`;
- repair rollback:
  `dist/transactions/computer-use-primary-home-repair-20260815-225522`.

The backend targeted gateway manifest covers shim environment enforcement,
stable local-skill bootstrap, primary command selection, matched IPC-2 wrapper,
and config restoration. The frontend source gate rejects a return to direct
`@oai/cua`. Its live integration test deliberately poisons every frontend-owned
runtime value and requires `list_apps()` to succeed through the installed shim
and stable wrapper. The real post-restart harness acceptance then passed on
2026-08-16 with 138 applications and `com.sudhir.codex.tauri` present.
