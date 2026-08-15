# Sudhir-Codex frontend update

This procedure rebuilds the independently namespaced Sudhir-Codex macOS app
from the currently installed official ChatGPT app.

The frontend can be updated more frequently than the Rust backend. The output
app launches the external backend at:

```text
/Users/sudhirjha/.playground/sudhir-codex/dist/sudhir-codex-core
```

It does not embed a second backend copy. Therefore a frontend refresh normally
takes about ten seconds and does not require Rust compilation.

## 1. When to run this procedure

Run it when:

- `/Applications/ChatGPT.app` has updated;
- the official app version or build changed;
- the Sudhir bootstrap, bundle metadata, entitlements, or signing procedure
  changed; or
- the proprietary UI and current forked app-server appear protocol-incompatible.

Do not rebuild the frontend merely because:

- a model was added or hidden;
- provider credentials changed;
- the gateway translation changed;
- the Cursor SDK worker or Composer route list changed;
- a new Rust backend was deployed and the existing app still passes its
  app-server and GUI checks.

## 2. Fixed paths and identities

| Purpose | Path or value |
|---|---|
| Frontend project | `/Users/sudhirjha/.playground/sudhir-codex-app` |
| Official source app | `/Applications/ChatGPT.app` |
| Installed app | `/Applications/Sudhir-Codex.app` |
| Custom icon master | `/Users/sudhirjha/.playground/sudhir-codex-app/Resources/SudhirCodexIcon.png` |
| Source Owl runtime | `/Applications/ChatGPT.app/Contents/MacOS/ChatGPT` |
| Build script | `/Users/sudhirjha/.playground/sudhir-codex-app/scripts/build` |
| Runtime Keychain migration guard | `/Users/sudhirjha/.playground/sudhir-codex-app/scripts/guard-runtime-keychain-migration` |
| Update status | `/Users/sudhirjha/.playground/sudhir-codex-app/scripts/frontend-status` |
| Quick check | `/Users/sudhirjha/.playground/sudhir-codex-app/scripts/quick-check` |
| Model-draft patch | `/Users/sudhirjha/.playground/sudhir-codex-app/scripts/patch-frontend-model-drafts.mjs` |
| Framework adapter | `/Users/sudhirjha/.playground/sudhir-codex-app/scripts/adapt-framework-version` |
| Official bundle ID | `com.openai.codex` |
| Sudhir bundle ID | `com.sudhir.codex` |
| Signing secret | AWS Secrets Manager `alamelu/pi-codesign` |
| AWS region | `ap-south-1` |
| CLI launcher | `/Users/sudhirjha/.local/bin/sudhir-codex` |
| Private Codex state | `/Users/sudhirjha/.sudhir-codex` |
| Electron profile | `/Users/sudhirjha/Library/Application Support/Sudhir-Codex` |

Never modify the official app in place. The build uses a staged APFS clone.

## 2A. Ask Sudhir-Codex to check and update herself

Use this instruction in a Sudhir-Codex task:

```text
Read ~/.codex/AGENTS.md and
~/.playground/sudhir-codex/documents/FRONTEND-UPDATE.md completely.
Run ~/.playground/sudhir-codex-app/scripts/frontend-status.
If status is update-available, complete the runtime/framework compatibility
preflight before quitting or building. Proceed only when the source framework
exactly matches the source Owl runtime target. Build and statically verify an
external candidate before quitting. If its signed runtime CDHash differs from
the installed runtime, stop and ask me to authorize that exact transition and
the one-item `Codex Storage Key` ACL migration. Update only the Sudhir-Codex
frontend from /Applications/ChatGPT.app using that runbook. Do not rebuild
Rust, do not stop official ChatGPT, preserve and verify a dated rollback app,
run the static and runtime checks, and do not delete the rollback without my
approval. Sudhir-Codex may restart herself through the documented recoverable
detached transaction.
```

The status command is read-only:

```bash
/Users/sudhirjha/.playground/sudhir-codex-app/scripts/frontend-status
```

Interpret its first line as follows:

- `status=up-to-date`: report the two matching versions and stop.
- `status=update-available`: a newer build exists; continue to the
  runtime/framework compatibility preflight. This status alone does not
  authorize a build.
- `status=official-build-older`: do not downgrade automatically.
- `status=version-mismatch-needs-review`: inspect the version strings and
  builds before changing anything.
- `status=identity-or-signature-invalid`: stop; do not clone or sign an
  untrusted or incorrectly namespaced source.
- Either `*-missing` status: stop and report the missing app.

## 2B. Current working installation — 2026-08-11

The working Sudhir-Codex installation is pinned to:

| Item | Current value |
|---|---|
| Sudhir-Codex version/build | `26.727.51351` / `6119` |
| Bundle identifier | `com.sudhir.codex` |
| Runtime identifier | `com.sudhir.codex.runtime` |
| Runtime CDHash | `bcc271f26306a5ded6e380268224821f5ec14ca6` |
| Framework | `150.0.7871.182` |
| ASAR SHA-256 | `45ac2b197f14bee42f3b5ead4d8056ae2022a105eac05029308ef1ce61604b72` |
| Model search | absent; restored pre-model-search frontend |
| Official app currently available | `26.803.41515` / `6321` |

The newer official build has not been adopted. Its presence produces
`status=update-available`, which does not authorize replacing the known-good
`6119` installation.

Computer Use was revalidated through the active plugin wrapper on 2026-08-11:
`list_apps()` returned 139 applications including `com.sudhir.codex`, and
`get_app_state({ app: "com.sudhir.codex" })` returned the real window tree and
screenshot. The configured helper must be spawned directly by Sudhir-Codex's
trusted Node runtime. LaunchServices startup assigns the helper its own macOS
privacy identity and loses Sudhir-Codex's existing Screen Recording grant.

Keep only this verified full-app rollback unless a future replacement
transaction temporarily needs another:

```text
/Users/sudhirjha/.playground/sudhir-codex-app/dist/backups/Sudhir-Codex.pre-model-search-poc-20260810-220749.app
```

Its version/build, identifiers, runtime CDHash, framework, ASAR hash, and deep
signature match the installed app. The compact Computer Use wrapper/service
rollback is under
`dist/transactions/computer-use-direct-child-20260811-003543`.

## Shared-primary Computer Use boundary — 2026-08-16

The standard ChatGPT frontend now launches against the shared primary state at
`~/.sudhir-codex`; `~/.sudhir-codex-chatgpt` remains only the preserved legacy
frontend/runtime location. The current bundled Computer Use plugin is
`1.0.1000717`.

The official `26.810.52044` Computer Use client speaks
`CodexComputerUseIPC-3`, while the installed signed Sudhir helper intentionally
still speaks `CodexComputerUseIPC-2`. The stable wrapper therefore loads the
preserved IPC-2 `create_client.js` from
`~/.sudhir-codex-chatgpt/frontend-control-runtime/legacy-cua`, not the current
official `@oai/cua` client. The stable wrapper SHA-256 is
`f92046877983b72c9b1f8dbafcb87640b6adb6482563a0c0db2b02a027414a6b`.

The earlier 2026-08-15 repair used a fixed 1.5-second post-launch rewrite. The
`26.810.52044` frontend wrote its upstream helper settings three seconds after
launch, so that timer did not protect the live harness. The timer is not part
of the durable boundary.

The upgrade-survival boundary is now owned outside frontend-managed config and
plugin caches:

- `~/.sudhir-codex/control/bin/sudhir-primary-node-repl` overrides poisoned or
  missing frontend values before it executes the signed legacy `node_repl`;
- `~/.sudhir-codex/control/computer-use-client.mjs` is the stable IPC-2 wrapper;
- `~/.sudhir-codex/skills/computer-use/SKILL.md` imports that stable wrapper;
- `CODEX_NODE_REPL_PATH` and `[mcp_servers.node_repl].command` both select the
  shim, so the frontend reinforces the same command during plugin refresh;
- plugin-cache patches remain compatibility copies only and are not required
  for the stable skill to work.

The shim SHA-256 is
`8f1dc3cc46c79eab0fdf03174c744eefb7fce6bac9a56bfdb95987a93f59c018`.
It supplies `CODEX_HOME=/Users/sudhirjha/.sudhir-codex`, the preserved Node
runtime, trusted-code roots, and both `SKY_CUA_*` and `SUDHIR_CUA_*` helper and
socket settings at the executable boundary.

Before accepting a frontend or backend upgrade, run:

```bash
cd ~/.playground/sudhir-codex
.venv/bin/python scripts/tests/sudhir_targeted_regressions.py gateway

cd ~/.playground/sudhir-codex-app
node --test tests/transition-launcher-source.test.mjs
scripts/verify-control-components build <new-control-build-directory>
SUDHIR_CONTROL_BUILD_DIR=dist/control-components-test-20260807-1 \
SUDHIR_CONTROL_TEST_CUA_LAUNCH=1 \
node --test --test-concurrency=1 \
  --test-name-pattern='stable primary Computer Use boundary ignores poisoned frontend environment' \
  tests/control-components.integration.test.mjs
```

The integration gate deliberately supplies wrong `CODEX_HOME`, Node, helper,
socket, and trusted-code values. It passes only when the installed shim
overrides them and `sky.list_apps()` succeeds through the stable wrapper. Final
runtime acceptance must additionally use the real post-restart harness. That
acceptance passed on 2026-08-16: the harness reported 138 applications and
confirmed that `com.sudhir.codex.tauri` was present.

The owner-approved cleanup on 2026-08-11 removed 50 obsolete top-level
artifacts: 25 redundant app rollbacks, 11 abandoned candidates, 6 completed
self-update copies, and 8 superseded transaction directories. Do not recreate
those historical copies merely for provenance; the dated implementation and
incident documents retain the required history.

## 2C. Self-update boundary

There is one unavoidable self-update boundary: the build refuses to replace
`/Applications/Sudhir-Codex.app` while Sudhir-Codex is running. Build and
statically verify an external candidate while the current app is still open.
Compare installed and candidate runtime CDHashes then; if they differ, obtain
the owner's explicit approval and migrate only the `Codex Storage Key` / `Codex`
ACL before arranging any quit or launch. Create and
verify the rollback before quitting. A detached installer may then wait for the
exact `Sudhir-Codex.app/Contents/MacOS/ChatGPT.real` process to exit, re-run the
runtime migration guard with the approved exact transition, install the
already-verified candidate, relaunch it, and run `scripts/quick-check`. On
failure after candidate installation it must preserve the candidate and
restore the verified rollback before reopening Sudhir-Codex. Never use
`killall ChatGPT`: that can also stop the official app.

### Safe self-update detachment

Use a temporary LaunchAgent bootstrapped into the current GUI domain. Its plist
must have a unique label, `RunAtLoad=true`, `KeepAlive=false`, absolute paths
for the wrapper and log files, and either an explicit `PATH` or a wrapper that
exports one containing the resolved `node` and `aws` directories. Record those
locations while still inside the running app:

```bash
command -v node
command -v aws
```

Do not use `nohup`. On 2026-07-31, quitting Sudhir-Codex terminated a
`nohup` child with the rest of the app process tree; its log stopped after the
startup lines and no rollback or build was created. Do not use
`launchctl submit` either: it creates an inferred keepalive job and uses
launchd's restricted default `PATH`, which may omit both NVM's `node` and
Homebrew's `aws`.

Before quitting the app:

1. Read the source framework's real `CFBundleShortVersionString` and require it
   to equal the framework version embedded in the source Owl runtime and recorded
   in `scripts/build`. A directory or install-name rewrite is not ABI proof. If
   the versions differ, stop before cloning, signing, quitting, or replacing.
2. Test the fail-closed ASAR patch on an APFS clone of the new official
   `app.asar`. Run both `patch` and `verify` on the clone. This covers the model
   draft, early/deferred app name, and protected-import guard. If any matcher
   fails, rebase and re-test it before closing Sudhir-Codex.
3. Build and statically verify an external candidate under `dist/candidates`
   while Sudhir-Codex is still running. Do not launch it.
4. Compare the installed and candidate `ChatGPT.real` CDHashes. If they differ,
   `guard-runtime-keychain-migration` must stop. Continue only after the owner
   explicitly authorizes the exact transition and the one-item ACL migration
   for Keychain service `Codex Storage Key`, account `Codex`. Complete that
   migration before installing or launching the candidate.
5. Create and signature-verify the dated APFS rollback while Sudhir-Codex is
   still running. Record its absolute path in the wrapper.
6. Create a wrapper that waits until no command begins with the exact prefix
   `/Applications/Sudhir-Codex.app/Contents/MacOS/ChatGPT.real`, then creates
   an explicit `candidate_installed` phase, installs the verified candidate,
   opens the new app, and runs `scripts/quick-check`. The wrapper must invoke
   the migration guard again before replacement.
7. Make the wrapper log every non-secret result. If failure occurs before
   candidate installation, verify and reopen the unchanged installed app. If
   failure occurs after candidate installation, stop only the candidate,
   preserve it under a unique `dist/backups/*.failed.app` path, restore and
   verify the rollback at the canonical path, and open the rollback.
8. Bootstrap the plist with
   `launchctl bootstrap "gui/$(id -u)" /absolute/path/to/job.plist`.
9. Run `launchctl print "gui/$(id -u)/<label>"` and require a running PID and
   `properties = runatload` with no `keepalive` property.
10. Quit only bundle ID `com.sudhir.codex` normally. Do not manually relaunch it
   while the job is waiting; let the wrapper perform the relaunch.

Never restore only when the installed directory is absent. A failed candidate
normally remains at `/Applications/Sudhir-Codex.app`; reopening that path
without restoring the rollback repeats the crash.

After relaunch, require the wrapper log to end in success, confirm the
LaunchAgent's `last exit code = 0`, and unload the completed one-shot job with
`launchctl bootout "gui/$(id -u)/<label>"`. Keep the readable log and dated
rollback until validation and cleanup approval are complete.

Because quitting Sudhir-Codex ends the active agent turn, final GUI checks are
performed after relaunch in a follow-up turn. Self-restart is a supported
operation and must not be avoided merely because official ChatGPT is available.
Official ChatGPT is the independent intervention path if the detached job
cannot complete or restore its rollback.

## 3. Safety invariants

- Official ChatGPT and Sudhir-Codex must remain separately namespaced.
- Never claim the official `codex://` URL scheme.
- Never share the official Electron user-data directory or singleton socket.
- Never enable Sparkle updates inside the cloned app.
- Never copy official Codex state into the Electron profile.
- Never symlink either app, state directory, auth file, or backend.
- Do not stop official ChatGPT.
- Do not use `killall ChatGPT`; both apps contain ChatGPT-named processes.
- Build into staging, verify it completely, and replace only the installed
  `/Applications/Sudhir-Codex.app`.
- Require the source framework version to match the source Owl runtime target
  exactly before quitting or replacement. Never disguise a different framework
  version by renaming its directory.
- Treat every installed-to-candidate runtime CDHash change as a login-Keychain
  migration event. Stop before replacement or launch unless the owner approves
  the exact transition and the one-item `Codex Storage Key` ACL migration.
- Keep a rollback app until the owner approves its deletion.

## 4. Preflight

Read:

```text
/Users/sudhirjha/.codex/AGENTS.md
/Users/sudhirjha/.playground/sudhir-codex/documents/README.md
/Users/sudhirjha/.playground/sudhir-codex-app/BUILD-RUNBOOK.md
/Users/sudhirjha/playground/alamelu/documents/alpi-signing-eli5.md
```

Verify required inputs:

```bash
test -x /Applications/ChatGPT.app/Contents/MacOS/ChatGPT
test -x /Users/sudhirjha/.local/bin/sudhir-codex
test -x /Users/sudhirjha/.playground/sudhir-codex/dist/sudhir-codex-core
test -d /Users/sudhirjha/.sudhir-codex
test -d /Users/sudhirjha/.playground/sudhir-codex
test -f /Users/sudhirjha/.playground/sudhir-codex-app/Resources/entitlements.mac.plist
test -f /Users/sudhirjha/.playground/sudhir-codex-app/Resources/SudhirCodexIcon.png
test -f /Users/sudhirjha/.playground/sudhir-codex-app/Sources/SudhirCodexBootstrap/main.c
test -f /Users/sudhirjha/.playground/sudhir-codex-app/scripts/patch-frontend-model-drafts.mjs
test -x /Users/sudhirjha/.playground/sudhir-codex-app/scripts/patch-safe-storage-identity
test -x /Users/sudhirjha/.playground/sudhir-codex-app/scripts/guard-runtime-keychain-migration
test -x /Users/sudhirjha/.playground/sudhir-codex-app/scripts/adapt-framework-version
test -x /Users/sudhirjha/.playground/sudhir-codex-app/scripts/frontend-status
command -v aws jq node xcrun codesign security plutil perl sips iconutil
aws sts get-caller-identity
```

Run the read-only comparison first:

```bash
/Users/sudhirjha/.playground/sudhir-codex-app/scripts/frontend-status
```

Verify the official source signature and capture its version:

```bash
codesign --verify --deep --strict --verbose=2 /Applications/ChatGPT.app
/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
  /Applications/ChatGPT.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
  /Applications/ChatGPT.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' \
  /Applications/ChatGPT.app/Contents/Info.plist
```

The source identifier must be `com.openai.codex`.

Before cloning, signing, quitting Sudhir-Codex, or replacing the installed app,
require the source framework version to match the source Owl runtime target:

```bash
source_framework_version="$(
  /usr/bin/plutil -extract CFBundleShortVersionString raw \
    '/Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Versions/Current/Resources/Info.plist'
)"
expected_runtime_framework_version="$(
  sed -n 's/^expected_runtime_framework_version="\([^"]*\)"/\1/p' \
    /Users/sudhirjha/.playground/sudhir-codex-app/scripts/build
)"
test -n "$source_framework_version"
test "$source_framework_version" = "$expected_runtime_framework_version"
```

If the equality check fails, stop. The source may be adopted later only by
advancing the compatible source runtime, framework target, runtime hashes,
identity, and Keychain handling together in a reviewed change. The check is
not a permanent prohibition on that source framework version.

Check the backend before repackaging:

```bash
shasum -a 256 \
  /Users/sudhirjha/.playground/sudhir-codex/dist/sudhir-codex-core
/Users/sudhirjha/.local/bin/sudhir-codex doctor
```

Before a self-update quits the running app, complete the non-interactive
AWS/keychain signing preflight in section 7. Never ask the owner for a
keychain password.

## 5. Back up the current Sudhir app

Create and verify the rollback before asking a self-updating Sudhir-Codex to
quit. Leave official ChatGPT running. Quit only Sudhir-Codex after the rollback
and detached recovery job are both verified; the build refuses to replace the
installed bundle while its main process is running.

If the installed app exists, create a dated APFS copy-on-write rollback before
running the build:

```bash
frontend_root=/Users/sudhirjha/.playground/sudhir-codex-app
installed_app=/Applications/Sudhir-Codex.app
stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$frontend_root/dist/backups"
/bin/cp -cR \
  "$installed_app" \
  "$frontend_root/dist/backups/Sudhir-Codex.pre-update-$stamp.app"
codesign --verify --deep --strict \
  "$frontend_root/dist/backups/Sudhir-Codex.pre-update-$stamp.app"
```

Do not use a symlink for rollback.

Record the current output version and identifier before replacement:

```bash
/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
  "$installed_app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
  "$installed_app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' \
  "$installed_app/Contents/Info.plist"
```

## 6. Build and sign

Build a signed external candidate first. The path must be new and remain under
`dist/candidates`:

```bash
candidate_app=/Users/sudhirjha/.playground/sudhir-codex-app/dist/candidates/Sudhir-Codex-candidate.app
test ! -e "$candidate_app"
SUDHIR_CODEX_OUTPUT_APP="$candidate_app" \
  /Users/sudhirjha/.playground/sudhir-codex-app/scripts/build
```

External-candidate mode signs and verifies the app but does not replace,
register, or launch `/Applications/Sudhir-Codex.app`.

The script:

1. Validates the official source app, exact runtime/framework version match,
   backend launcher, state, gateway, bootstrap source, entitlements, and 2048px
   alpha icon master.
2. Creates a temporary staging directory under `dist`.
3. Uses `cp -cR` for an APFS copy-on-write clone.
4. Applies and verifies the same-length ASAR changes for route-draft model
   selection, the early/deferred `Sudhir-Codex` application name, and the
   protected browser-profile import guard.
5. Verifies the source Owl runtime's SHA256, CDHash, identifier, signature,
   executable text, and embedded framework target, then carries it into the
   clone as `ChatGPT.real`.
6. Verifies that the matching staged framework uses the exact version path
   embedded in that runtime. Path/install-name normalization is
   permitted only after the source metadata passes the exact-version guard; it
   must never disguise a different framework version.
7. Compiles the small arm64 bootstrap executable with `xcrun clang`.
8. Generates the complete macOS iconset and compiles the Unified Prism
   `.icns`.
9. Replaces the Finder, Dock, and frontend fallback icon resources before
   signing.
10. Sets the outer identity to `com.sudhir.codex`.
11. Rewrites nested framework, helper, alert, renderer, and Dock tile bundle IDs
   into the `com.sudhir.codex` namespace.
12. Removes official URL handling, document associations, exported UTI, and the
   Sparkle public key.
13. Sets private `CODEX_HOME`, CLI, gateway, and Electron profile paths.
14. Disables Sparkle.
15. Requires exactly one null-delimited `com.openai.codex` Chromium
    product-directory marker and changes it to same-length
    `com.sudhir.codex`.
16. Retrieves the signing identity, keychain path, and keychain password from
    AWS Secrets Manager.
17. Unlocks the dedicated signing Keychain and sets its code-signing partition
    list.
18. Signs the framework, Dock plugin, runtime (as
    `com.sudhir.codex.runtime`), and outer app.
19. Strictly verifies the complete staged app, runtime CDHash, and identifiers.
20. For direct-install mode, compares the installed and candidate runtime
    CDHashes and stops before replacement unless any change has exact owner
    authorization.
21. Installs only the verified app at `/Applications/Sudhir-Codex.app` and
    registers that canonical path with Launch Services. External-candidate mode
    stops after moving the verified app under `dist/candidates`.

If the marker count is not exactly one after an official update, stop. Inspect
the new framework layout and update the build deliberately; never apply a
blind binary replacement.

The ASAR patch is deliberately fail-closed. It requires exact upstream matches
for model-draft behavior, the early/deferred app identity, and the
protected-import guard. It keeps every changed entry and the complete ASAR at
their original byte lengths, then recalculates each file SHA256 and block hash.
If an official frontend update changes an expression, the build stops before
signing or replacing the installed app. Rebase the affected transformation;
never weaken the match counts.

ASAR `productName` and `CFBundleName` do not determine Electron's startup
Keychain item. The item used by Sudhir-Codex is service `Codex Storage Key`,
account `Codex`. The strings changed by `patch-safe-storage-identity` belong to
the protected browser-profile import paths and do not update this item's ACL.

On 2026-08-02, build `5848` used runtime CDHash
`afb5e74a2fa9bd550bbc07d9e45f9c4561971f6f`; build `6119` used
`bcc271f26306a5ded6e380268224821f5ec14ca6`. Unified logs showed the existing
login-Keychain ACL contained only the old hash, rejected the new hash, and
displayed two password prompts. The owner denied both; the app and app-server
continued. The same local certificate signed both runtimes, but it has no Team
ID, so signing did not make the ACL identity stable across CDHash changes.
A one-item ACL migration was then completed successfully: the `Codex Storage
Key` / `Codex` item retained the old hash and added the current build `6119`
hash. On the next ordinary start, Sudhir-Codex opened without either password
prompt. No official ChatGPT Keychain item was changed.

`scripts/guard-runtime-keychain-migration` now treats any such change as a
migration event. The normal build and versioned detached installer must stop
before replacement or launch unless the owner explicitly authorizes the exact
`old-cdhash->new-cdhash` transition and the one-item ACL migration. Perform the
migration before or as part of installation, before the new runtime is launched,
so ordinary starts do not prompt. The AWS signing Keychain cannot silently
rewrite the user's login-Keychain ACL. Never use
`--use-mock-keychain`; it replaces Keychain-backed encryption with a fixed test
key rather than repairing the ACL.

## 7. Signing details

The sanctioned signing protocol comes from:

```text
/Users/sudhirjha/playground/alamelu/documents/alpi-signing-eli5.md
/Users/sudhirjha/playground/alamelu-pi-gui/apps/desktop/scripts/package-alpi-dir.mjs
/Users/sudhirjha/playground/alamelu-tauri/apps/desktop/scripts/sign-tauri-candidate.mjs
```

AWS is the source of truth:

```text
AWS secret ID: alamelu/pi-codesign
Region:        ap-south-1
Required keys: identity_hash, keychain_path, keychain_password
Informational: identity_name
```

The owner does not supply or know this signing-Keychain password. Never ask for it.
Never print secret values, enable shell tracing around them, write them to a
log or environment file, or embed them in the app. Do not inspect the keychain
with `security show-keychain-info`, `security find-certificate`, or similar
commands; those can trigger password UI. If a password prompt appears during
build or signing, stop: the signing flow is wrong. A login-Keychain prompt after
launch is a separate runtime migration event and is permitted only after the
owner approved that exact CDHash transition.

Before closing a running Sudhir-Codex self-update, validate AWS access and
unlock the explicit keychain non-interactively. This is also the credential
handling contract implemented by `scripts/build`:

```bash
set +x
signing_secret_json="$(
  aws secretsmanager get-secret-value \
    --region ap-south-1 \
    --secret-id alamelu/pi-codesign \
    --query SecretString \
    --output text
)"
signing_identity="$(
  printf '%s' "$signing_secret_json" | jq -er '.identity_hash'
)"
signing_keychain="$(
  printf '%s' "$signing_secret_json" | jq -er '.keychain_path'
)"
signing_keychain_password="$(
  printf '%s' "$signing_secret_json" | jq -er '.keychain_password'
)"
test -n "$signing_identity"
test -f "$signing_keychain"
security unlock-keychain \
  -p "$signing_keychain_password" \
  "$signing_keychain" >/dev/null
security set-key-partition-list \
  -S "apple-tool:,apple:,codesign:" \
  -s \
  -k "$signing_keychain_password" \
  "$signing_keychain" >/dev/null
unset signing_secret_json signing_keychain_password
```

Fail closed if AWS retrieval, JSON validation, keychain existence, unlock, or
partition-list setup fails. Do not fall back to an unsigned build, a different
keychain, raw `codesign`, or the duplicated friendly certificate name
`Alamelu Pi Local Code Signing`.

Every signing command must use the AWS-provided values explicitly:

```text
--keychain <keychain_path>
--sign <identity_hash>
--timestamp=none
```

This is local, non-notarized signing. Do not request a timestamp.

Signing order is significant:

1. `Codex Framework.framework`, deep with hardened runtime and entitlements.
2. `CodexDockTilePlugin.plugin`, hardened runtime.
3. `ChatGPT.real`, hardened runtime and entitlements, explicitly identified as
   `com.sudhir.codex.runtime`.
4. Outer `Sudhir-Codex.app`, without `--deep`, with hardened runtime and
   entitlements, sealing the already-signed nested components.

This is a local signing identity, not an Apple-notarized Developer ID release.

## 8. Static validation before launch

Run:

```bash
new_app=/Users/sudhirjha/.playground/sudhir-codex-app/dist/candidates/Sudhir-Codex-candidate.app
expected_runtime_cdhash="$(
  sed -n 's/^expected_signed_runtime_cdhash="\([^"]*\)"/\1/p' \
    /Users/sudhirjha/.playground/sudhir-codex-app/scripts/build
)"
expected_framework_version="$(
  sed -n 's/^expected_runtime_framework_version="\([^"]*\)"/\1/p' \
    /Users/sudhirjha/.playground/sudhir-codex-app/scripts/build
)"

codesign --verify --deep --strict --verbose=2 "$new_app"
test "$(
  codesign -d --verbose=4 "$new_app/Contents/MacOS/ChatGPT.real" 2>&1 | \
    awk -F= '/^CDHash=/{print $2; exit}'
)" = "$expected_runtime_cdhash"
/Users/sudhirjha/.playground/sudhir-codex-app/scripts/adapt-framework-version \
  verify \
  "$new_app/Contents/Frameworks/Codex Framework.framework" \
  "$expected_framework_version"
/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
  "$new_app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' \
  "$new_app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :LSEnvironment:CODEX_CLI_PATH' \
  "$new_app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :LSEnvironment:CODEX_HOME' \
  "$new_app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c \
  'Print :LSEnvironment:CODEX_ELECTRON_USER_DATA_PATH' \
  "$new_app/Contents/Info.plist"
sips -g hasAlpha -g pixelWidth -g pixelHeight \
  "$new_app/Contents/Resources/icon-chatgpt.png"
shasum -a 256 \
  /Users/sudhirjha/.playground/sudhir-codex-app/Resources/SudhirCodexIcon.png \
  "$new_app/Contents/Resources/icon-chatgpt.png" \
  "$new_app/Contents/Resources/default_app/icon.png"
node \
  /Users/sudhirjha/.playground/sudhir-codex-app/scripts/patch-frontend-model-drafts.mjs \
  verify \
  "$new_app/Contents/Resources/app.asar"
```

Expected values:

```text
CFBundleIdentifier:                  com.sudhir.codex
CFBundleExecutable:                  Sudhir-Codex
CODEX_CLI_PATH:                      /Users/sudhirjha/.local/bin/sudhir-codex
CODEX_HOME:                          /Users/sudhirjha/.sudhir-codex
CODEX_ELECTRON_USER_DATA_PATH:       /Users/sudhirjha/Library/Application Support/Sudhir-Codex
Custom icon PNG:                     alpha-enabled, 2048x2048, identical hashes
```

Confirm the official app remains valid and separate:

```bash
codesign --verify --deep --strict /Applications/ChatGPT.app
/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
  /Applications/ChatGPT.app/Contents/Info.plist
```

Before replacement or launch, compare the installed and candidate runtime
CDHashes through the guard:

```bash
installed_runtime_cdhash="$(
  codesign -d --verbose=4 \
    /Applications/Sudhir-Codex.app/Contents/MacOS/ChatGPT.real 2>&1 | \
    awk -F= '/^CDHash=/{print $2; exit}'
)"
candidate_runtime_cdhash="$(
  codesign -d --verbose=4 "$new_app/Contents/MacOS/ChatGPT.real" 2>&1 | \
    awk -F= '/^CDHash=/{print $2; exit}'
)"
/Users/sudhirjha/.playground/sudhir-codex-app/scripts/guard-runtime-keychain-migration \
  "$installed_runtime_cdhash" \
  "$candidate_runtime_cdhash"
```

Equal hashes pass. Different hashes exit `77`, print the exact transition, and
stop the procedure. Do not supply the third authorization argument or arrange a
restart until the owner approves that transition and the one-item ACL migration.
The guard deliberately does not query Keychain. An equal-hash result proves
only that the candidate introduces no additional runtime identity change; it
does not prove that the current login-Keychain ACL admits that hash.
After approval, use a versioned detached installer that pins both builds and
hashes, re-runs this guard, preserves a verified rollback, and requires the
explicit migration action. `scripts/install-6119-keychain-v2` is the concrete
`5848`→`6119` example; do not reuse its pinned values for a later release. A
future Owl CDHash change needs the same explicit migration of only `Codex
Storage Key` / `Codex`, retaining the already-authorized hashes and adding the
new hash, unless Sudhir-Codex is later signed with a stable Team ID.

## 9. Launch and automated quick checks

Only after the verified candidate has been installed through that transaction,
launch one ordinary Sudhir-Codex instance:

```bash
open /Applications/Sudhir-Codex.app
/Users/sudhirjha/.playground/sudhir-codex-app/scripts/quick-check
```

Do not use `open -n` for the routine check. Multiple Electron processes sharing
the same Sudhir bundle identity can participate in one application lifecycle;
quitting the extra instance may quit the existing Sudhir instance as well.

The quick check validates:

- official and Sudhir signatures;
- separate bundle identifiers;
- private launcher, state, and Electron paths;
- the expected signed Sudhir runtime CDHash and identifier;
- the Sudhir runtime process;
- the external forked app-server process;
- current model, effort, sandbox, and approval defaults;
- recent gateway routes.

Inspect processes without matching by the generic name `ChatGPT`:

```bash
ps -axo pid,ppid,lstart,command | \
  rg '/Applications/ChatGPT.app/Contents/MacOS/ChatGPT|Sudhir-Codex.app/Contents/MacOS/ChatGPT.real|sudhir-codex-core.*app-server'
```

The Sudhir app-server command must point to:

```text
/Users/sudhirjha/.playground/sudhir-codex/dist/sudhir-codex-core
```

## 10. Manual GUI validation

Record the configured model defaults before touching the picker:

```bash
grep -E '^(model|model_reasoning_effort) = ' \
  /Users/sudhirjha/.sudhir-codex/config.toml
```

Do not rely on the full file hash alone. App startup may update unrelated
plugin-marketplace metadata, including
`marketplaces.openai-bundled.last_updated`.

In the rebuilt app:

1. Open a new task.
2. Open the model picker.
3. Confirm the filtered merged catalog loads.
4. Confirm excluded providers and models remain absent according to
   `model-visibility.json`.
5. Confirm the four expected Composer entries render when Cursor support is
   enabled:
   `Composer 2.5 Fast`, `Composer 2.5 Slow`, `Composer Latest Fast`, and
   `Composer Latest Slow`.
6. Select one currently available GPT model and request an exact short token.
7. Open a separate new-task draft, select a quick open model such as direct
   DeepSeek, wait a few seconds, and confirm the picker does not snap back.
8. Start that task and
   request another exact short token.
9. Confirm both responses render and tools remain available.
10. On a model that advertises them, confirm Max and Ultra appear directly.
11. Change the model in the created task and confirm that task shows the new
    selection.
12. Confirm neither picker operation altered the `model` or
    `model_reasoning_effort` keys in the global config.
13. Locate the Sudhir desktop log by its main-process PID. Confirm new-task
    selection emitted neither `Setting default model` nor
    `method=config/batchWrite`; confirm the existing-task change emitted
    `method=thread/settings/update`.

The desktop may issue a separate GPT request for automatic thread-title
generation. It is not evidence that the task turn used GPT. Confirm the task
model from the successful gateway route and the rendered response.

At the 2026-07-24 validation, the picker contained 48 intentionally visible
models. Treat that as a baseline. Compare future counts with the visibility
policy and current provider catalogs instead of forcing the number to remain
48. After the four Cursor routes and current GPT catalog were present, the
2026-07-25 frontend validation contained 52 visible models.

Avoid NVIDIA's free route for the routine GUI smoke because it can be very
slow.

When automating the native macOS model menu, an accessibility click on an item
that is not keyboard-focused can dismiss the menu without selecting it. Focus
the intended item with the arrow keys, press Return, and verify that the
composer button displays the intended model before sending the smoke prompt.
This is an automation gotcha, not a user-facing picker failure.

## 11. Runtime and protocol compatibility

### Runtime/framework compatibility

The source Owl runtime and `Codex Framework.framework` are one binary
compatibility unit. Static ASAR integrity, code signatures, bundle identity,
and a rewritten framework path do not prove ABI compatibility.

On 2026-08-01, the runtime from build `5848` targeted
`150.0.7871.128`. Framework `150.0.7871.182` from builds `6067` and
`6119` was renamed to the `.128` path and passed every static check, but both
candidates trapped immediately in `ChromeMain`/V8. The build now refuses a
source framework whose real version differs from the recorded source-runtime
target.

The incident evidence and future-upgrade requirements are preserved in:

```text
/Users/sudhirjha/.playground/sudhir-codex-app/INCIDENT-2026-08-01-FRAMEWORK-ABI.md
```

This exact-match rule does not freeze frontend upgrades. It requires a future
runtime and framework to advance together, with their identity, Keychain
behavior, hashes, staged launch, and rollback path reviewed as one change.

### Runtime Keychain ACL migration

The dedicated AWS-backed Keychain is used only to access the local signing
certificate. It is not the user's login Keychain and cannot update the ACL on
Electron's existing startup secret. With the current local certificate's lack
of a Team ID, that ACL may contain a runtime CDHash requirement.

Therefore certificate equality is not migration proof. A candidate whose
runtime CDHash differs from the installed app must remain unlaunched until the
owner approves the exact transition and a migration of only Keychain service
`Codex Storage Key`, account `Codex`. Retain the existing authorized hashes and
add the candidate hash before or during installation, before launching the new
runtime. The successful `5848`→`6119` migration retained
`afb5e74a2fa9bd550bbc07d9e45f9c4561971f6f` and added
`bcc271f26306a5ded6e380268224821f5ec14ca6`; official ChatGPT was unaffected.
Repeat this one-item procedure for future Owl CDHash changes unless signing
gains a stable Team ID. A password prompt during build, installation, or launch
means the migration path is incomplete. Never use `--use-mock-keychain`.

### App-server protocol compatibility

The proprietary frontend talks to the fork through the open-source app-server
protocol. A newer frontend can break if its expected protocol is newer than the
deployed backend.

Signals of incompatibility include:

- app-server exits immediately;
- the model picker fails to load;
- task creation or resume requests fail;
- config RPC errors appear;
- notifications or tool calls stop rendering.

If static signing checks pass but runtime protocol checks fail:

1. Keep the previous frontend rollback.
2. Capture app and app-server logs.
3. Compare the installed official app date/version with the fork's upstream
   Codex revision.
4. Decide whether the backend monthly upgrade must be brought forward.
5. Do not weaken namespace isolation or signing to hide a protocol failure.

## 12. Rollback

If the new app fails:

1. Quit only Sudhir-Codex.
2. Preserve the failed app and logs.
3. Restore the dated rollback app to
   `/Applications/Sudhir-Codex.app`.
4. Verify the restored signature.
5. Launch it and run the quick check.

A detached self-update must track whether candidate installation completed.
If failure occurs after installation, the failed candidate still normally
occupies `/Applications/Sudhir-Codex.app`. Stop it, move it to a unique
`dist/backups/Sudhir-Codex.failed-*.app` path, restore the verified rollback to
the canonical path, verify the rollback, and only then relaunch. Never use
“installed directory exists” as the recovery decision and never reopen an
unverified candidate from that path.

If the detached job cannot complete this recovery, use official ChatGPT to
inspect the preserved job log and restore the rollback. This is the independent
intervention path for a supported Sudhir-Codex self-restart.

Do not remove:

```text
/Users/sudhirjha/.sudhir-codex
/Users/sudhirjha/Library/Application Support/Sudhir-Codex
/Users/sudhirjha/.playground/sudhir-codex/dist/sudhir-codex-core
```

Those contain independent state or the external backend and are not part of a
frontend rollback.

## 13. Cleanup approval boundary

After successful static, process, GUI, GPT, open-model, and persistence checks:

```bash
du -sh /Users/sudhirjha/.playground/sudhir-codex-app/dist/backups
df -h /Users/sudhirjha/.playground/sudhir-codex-app
```

Ask the owner before deleting the previous app backup. Do not empty the user's
entire Trash as part of cleanup.

## 14. Frontend update summary template

```text
Date:
Official source version/build:
Sudhir installed version/build:
Build duration:
Signing identity hash:
Outer identifier:
Deep signature validation:
Official app still valid/running:
Sudhir app process:
External app-server process:
Quick-check result:
Picker/search result:
Visible model count:
GPT smoke:
Open-model smoke:
Max/Ultra check:
Config persistence check:
Rollback app:
Cleanup approved/completed:
Known warnings:
```

## 15. Verified frontend update on 2026-07-25

```text
Date:                              2026-07-25
Official source:                   26.721.41059 / 5848
Sudhir before:                     26.721.31836 / 5828
Sudhir after:                      26.721.41059 / 5848
Successful build duration:         11.22 seconds
Signing identity hash:             08D1A8559A49F375AFC426EDBB22A7DC4E0D9AA3
Outer identifier:                  com.sudhir.codex
Deep signature validation:         passed
Official app still valid/running:  yes
Sudhir app and app-server:         running
Quick-check result:                passed
Visible picker count:              52
Cursor entries:                    Composer 2.5 Fast/Slow; Latest Fast/Slow
GPT smoke:                         FRONTEND_5848_GPT_OK via GPT-5.6 Sol High
Open-model smoke:                  FRONTEND_5848_OPEN_OK via direct DeepSeek V4 Flash High
Existing-task model change:        thread/settings/update emitted; global default unchanged
Max/Ultra check:                   present for models that advertise them
Global defaults after picker use:  pi-xai/grok-4.5 / high
Backend SHA-256 after update:       dd24fa3643886b2e29eb31b8e906e0a45f1f8dc27b9c970719c70ee79e1e156f
Rollback app:                      dist/backups/Sudhir-Codex.pre-update-20260725-103227.app
Cleanup:                           not approved; rollback retained
```

The first build attempt stopped safely in 2.12 seconds because upstream changed
two minified local identifiers in the model-draft setter (`EYr` to `gYr` and
`Bf` to `Rf`). The installed app was untouched. The fail-closed transformation
was rebased against the new asset, tested on an APFS clone of the official
ASAR, checked for JavaScript syntax, complete SHA256/block integrity, and
idempotence, then used for the successful build.

## 16. Runtime/framework incident on 2026-08-01

Builds `6067` and `6119` both carried framework `150.0.7871.182`. Pairing that
framework with the pinned `150.0.7871.128` runtime by rewriting its path caused
the same immediate `SIGTRAP` twice. The second detached job then reopened the
failed candidate because its rollback handler restored only when the installed
directory was absent.

The installed app was restored to signed build `5848`; the failed `6119`
candidate and logs were preserved. The build now checks the source framework's
real version before staging or replacement. The complete evidence, mandatory
self-update state machine, and non-freezing future-upgrade path are recorded in
`/Users/sudhirjha/.playground/sudhir-codex-app/INCIDENT-2026-08-01-FRAMEWORK-ABI.md`.
