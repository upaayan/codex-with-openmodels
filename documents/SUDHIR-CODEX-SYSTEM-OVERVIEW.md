# Sudhir-Codex System Overview

**Audience:** System owner and repair operator

**Architecture:** Small transition launcher using the official ChatGPT app

**Last verified:** 20 August 2026

This document is the owner-level map of the current Sudhir-Codex desktop
system. The companion operational procedure is
[SUDHIR-CODEX-REPAIR-RUNBOOK.md](SUDHIR-CODEX-REPAIR-RUNBOOK.md).

## The short version

`/Applications/Sudhir-Codex.app` is not a second copy of Electron or ChatGPT.
It is a small signed launcher. It starts the official
`/Applications/ChatGPT.app`, but gives it Sudhir-Codex's private backend,
configuration, state directory, control runtime, and a separate browser
profile.

This design avoids maintaining a cloned desktop application. It cannot make the
official frontend immutable: OpenAI can update the official application, and
the running frontend or its plugin synchronizer can update writable state under
`~/.sudhir-codex`. Our launcher guard can detect unsafe or incomplete state and
stop startup, but a validation guard is not a filesystem write blocker.

Recovery is therefore based on four things:

1. identify which layer failed before changing anything;
2. keep the official application and Sudhir-owned state separate;
3. restore only the damaged managed entries from a versioned recovery point;
4. validate and copy each known-good recovery point to the backup server.

## 1. Components and how they interact

### 1.1 The current shape

```text
Click /Applications/Sudhir-Codex.app
                 |
                 v
Small signed Sudhir launcher
                 |
                 v
gpt-pro-frontend-transition ensure-primary
                 |
                 +---- validates/repairs required control settings
                 |
                 v
Official /Applications/ChatGPT.app UI
   with a separate Sudhir browser profile
                 |
                 v
Sudhir CLI wrapper -> private app-server -> Sudhir gateway/providers
                 |
                 +---- ~/.sudhir-codex state and model policy
                 |
                 +---- stable Browser/Chrome/Computer Use controls
```

There is only one ChatGPT/Electron frontend bundle in this path: OpenAI's
official application. Our launcher changes the environment and state roots
used by that frontend; it does not copy or edit the official application.

### 1.2 Component ownership

| Component | Owner | Purpose | Update behaviour |
|---|---|---|---|
| `/Applications/ChatGPT.app` | OpenAI | Electron UI, task composer, model picker, bundled plugins and Node resources | Can auto-update |
| `/Applications/Sudhir-Codex.app` | Sudhir-Codex | Small signed launcher; calls `ensure-primary` | Changes only when we deliberately replace the launcher |
| `scripts/gpt-pro-frontend-transition` | Sudhir-Codex | Validates the official app, repairs managed control wiring, and starts the official app with Sudhir paths | Maintained in the backend repository; no Rust build is needed |
| `dist/sudhir-codex-core` | Sudhir-Codex | Private app-server/backend used by the UI; currently `openmodels-v0.1.0-rc.13` | Built by GitHub Actions only for an approved backend change |
| `dist/codex-code-mode-host` and `dist/codex-responses-api-proxy` | Sudhir-Codex | Native helpers shipped with the same backend release | Activated and rolled back with the core binary as one release set |
| `~/.local/bin/sudhir-codex` | Sudhir-Codex | CLI wrapper selected by the official frontend | Maintained by us |
| `~/.sudhir-codex` | Sudhir-Codex, with some frontend/plugin writes | Primary config, sessions, model state, gateway state, plugins and controls | Writable live state; must be backed up |
| `~/Library/Application Support/Sudhir-Codex-ChatGPT` | Sudhir-Codex | Separate Electron/browser profile | Keeps official ChatGPT's normal profile isolated |
| `~/.sudhir-codex/control` | Sudhir-Codex | Stable control shims, helper, broker and socket | Intended to remain outside frontend-managed plugin caches |
| `~/.sudhir-codex-chatgpt/frontend-control-runtime/legacy-cua` | Sudhir-Codex | Preserved signed Node/Computer Use runtime required by the current control compatibility boundary | Replaced only deliberately |
| `~/.sudhir-codex/model-visibility.json` | Sudhir-Codex | Controls which catalog models are shown | Does not control whether a UI selection persists |
| `~/.sudhir-codex/.codex-global-state.json` | Frontend state | Remembers desktop UI/global state | May be rewritten by the frontend |
| `~/.sudhir-codex/gateway` | Sudhir-Codex | Provider routing logs and runtime state | Written by the gateway |
| `sudhir_codex/src/sudhir_codex_gateway/` | Sudhir-Codex | Python gateway source; translates Responses <-> provider APIs, route discovery, auth | Git-tracked in `~/.playground/sudhir-codex`; survives ChatGPT upgrades |

At the last verification, the official app was version `26.814.41407`, build
`6720`, bundle ID `com.openai.codex`. The small launcher was version `1.0.0`,
build `1`, bundle ID `com.sudhir.codex`. Both signatures were valid.

### 1.3 What the launcher supplies

The launcher runs the official UI with, among other values:

- `CODEX_HOME=/Users/sudhirjha/.sudhir-codex`;
- the Sudhir CLI/app-server path;
- the isolated `Sudhir-Codex-ChatGPT` browser profile;
- the stable Sudhir `node_repl` shim and control paths;
- the current official Browser client hash used by the trust allowlist.

This is why the UI can be official while the models, providers, sessions,
gateway and tools remain Sudhir-Codex's.

### 1.4 What this architecture deliberately does not contain

The installed launcher contains no copied `app.asar`, no `ChatGPT.real`, and no
private Electron framework. It is not the older 1.4 GB full-app clone.

Older documents in this repository describe cloning, patching and re-signing a
complete ChatGPT application. Those documents are historical for the current
desktop architecture. They must not be used for a routine repair unless the
owner separately authorizes an architecture change.

## 2. What goes wrong, and how

### 2.1 Official application updates change more than the version number

An official update may replace:

- model-picker and new-task draft behaviour in the frontend JavaScript;
- bundled Browser and Computer Use plugins;
- the official Browser client file, which changes its SHA-256 trust value;
- bundled Node resources and service paths;
- the timing or shape of plugin/configuration synchronization.

The small launcher itself is not overwritten by that update. It continues to
start whatever official `ChatGPT.app` is currently installed. Compatibility
assumptions can still become stale because the code it launches has changed.

### 2.2 Writable configuration can be rewritten

`~/.sudhir-codex/config.toml` is shared live state. Sudhir-Codex writes it, and
the official frontend's plugin/configuration synchronization may also write
parts of it after startup. A repair performed while an older process is still
running can also be overwritten when that process later flushes its in-memory
state.

The recurring startup failure was caused by this required entry disappearing:

```text
mcp_servers.node_repl.env.NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S
```

The launcher stopped before starting the backend because the control repair
code was deliberately fail-closed at that point. The live code now knows how to
reinsert that entry when its trusted-code anchor remains present.

### 2.3 Why the guard did not prevent the overwrite

The guard answers: “Is this configuration safe and complete enough to start?”
It does not own every write to the file.

Preventing an overwrite would require all writers to go through one mediator,
or making the file immutable. Neither is currently true. Making the whole file
immutable would also break legitimate model, plugin, marketplace and user
configuration updates. The correct role of the guard is to detect damage and
stop unsafe startup. The missing partner was a fast, deterministic recovery
path with current templates and backups.

### 2.4 A visible model and a persistent selection are different contracts

The model pipeline has separate stages:

1. the backend/gateway supplies a catalog;
2. `model-visibility.json` filters what appears;
3. the frontend keeps a new-task draft selection;
4. starting a task sends that selected model to app-server;
5. an existing task changes model through `thread/settings/update`.

A model can therefore appear correctly but still snap back to GPT before a task
is sent. Deep inspection of official ChatGPT build `6720` corrected the initial
diagnosis: a fresh-draft picker change *does* send `config/batchWrite` for
`model` and `model_reasoning_effort`. The pre-`rc.13` Sudhir app-server then
deliberately removed those two edits, returned an overridden result, and left
the GPT default in `config.toml`. The frontend reread that default and snapped
the picker back. The request reached the backend; the gateway and visibility
policy were not involved.

`openmodels-v0.1.0-rc.13` removes that discard policy. A new-task picker change
now returns `status=ok` and persists both fields as the defaults for future new
tasks. Existing-task switching remains separate and thread-scoped through
`thread/settings/update`.

This fix does not patch the official frontend and does not create a second
Electron application. The source-only `patch-frontend-model-drafts.mjs` work in
the separate app repository remains uninstalled and belongs to the rejected
full-clone path.

### 2.5 Status fields can be diagnostic without being repair orders

The transition status currently reports that the app, app-server and control
runtime are healthy. It also reports an old baseline/config mismatch and a
Chrome-manifest snapshot mismatch. Those two booleans compare mutable live
state with historical snapshots; they do not, by themselves, mean “restore the
old file.” Restoring an entire stale config can remove newer valid settings.

Likewise, the existing `frontend-status` command compares the official app's
version/build with the launcher version/build. Because one is ChatGPT and the
other is a small launcher, `status=update-available` is expected and is not an
instruction to build a cloned frontend. Use it only for the identity and
signature fields in this architecture.

## 3. How repairs work

Repairs begin by assigning the symptom to one layer. This avoids hours of work
on unchanged components.

| Symptom | First suspected layer | First repair action | Explicitly avoid |
|---|---|---|---|
| Launcher says a required `node_repl` setting is missing | Managed config/control wiring | Back up the live config, run `ensure-primary`, validate both trust entries against the current official Browser client | Rust build, gateway build, full Electron clone |
| App starts but models are missing | Catalog, cache or `model-visibility.json` | Validate those files and compare catalog/visibility evidence | Editing frontend draft code |
| Models appear but a fresh draft snaps back; `config/batchWrite` is followed by the old GPT default | App-server new-task default policy | Verify the response and config, then activate the known-good `rc.13` release set | Gateway build, visibility edits, full Electron clone, local Rust build |
| Models appear but no picker update RPC reaches app-server | Official frontend draft state | Record the official build and stop at the frontend boundary | Assuming the `rc.13` backend fix covers a different frontend failure |
| Existing task sends `thread/settings/update` and receives an error | App-server/gateway | Read the exact response and gateway route before changing code | Assuming frontend failure |
| Backend does not start | Launcher, CLI wrapper, deployed app-server or gateway process | Check paths, process tree and logs | Rebuilding before identifying a missing or stale file |
| Browser/Computer Use fails | Stable shim/control runtime or trust hash | Validate the shim, helper, socket and current hash | Editing plugin-cache copies first |

The detailed command sequence is in the repair runbook. Its central rule is:
restore the smallest damaged unit, then validate the real app. Never replace
the complete config merely because one old baseline hash differs.

## 4. Prevention and rapid recovery

### 4.1 What can be prevented

We can prevent an official update from directly replacing Sudhir-owned pieces
by keeping them outside the official bundle and plugin cache. The current
launcher, separate profile, primary state root, stable executable shim and
control directory do this.

We can also prevent a bad repair from becoming permanent by using:

- a pre-change copy of every file being repaired;
- a known-good repaired copy;
- a small managed-settings reference;
- file hashes and official-app version/build metadata;
- local versioned directories that are never overwritten;
- a byte-for-byte verified copy on the backup server.

### 4.2 What cannot be prevented by the launcher alone

The launcher cannot stop OpenAI from updating the official app or stop an
authorized frontend process from writing its own live state. It can validate
before launch and reapply known managed settings. When an official frontend
changes its RPC contract, the receiving Sudhir component must be diagnosed and
made compatible; the launcher cannot infer that compatibility automatically.

### 4.3 Current verified recovery points

The 20 August trust repair is stored locally at:

```text
/Users/sudhirjha/.sudhir-codex/config-backups/
trust-repair-20260820T035726Z/
```

It contains:

- `config.toml.before` — exact failed/pre-repair file;
- `config.toml.repaired` — first repair result;
- `config.toml.repaired-final` — final known-good config;
- `managed-trust-entries.toml` — only the four managed trust entries.

The same four files exist on the backup server at:

```text
/home/ubuntu/.sudhir-codex/config-backups/
trust-repair-20260820T035726Z/
```

Their local and remote SHA-256 values match. The final repaired config's hash
is `37276c90973570092e83850217e7719db821ab498fea0473ea0489c4ff2e7cea`.
The managed fragment's hash is
`4a245cd67254ee571a9a54fcdbc9f2b21dd13a69fdf864448ddbce970aa89f70`.

This recovery point is exact for official ChatGPT `26.814.41407` build `6720`.
Its Browser trust hash must not be copied blindly after a later official
update; the transition repair derives the new hash from the newly installed
official Browser client.

The working Mac backend is GitHub Actions release candidate
`openmodels-v0.1.0-rc.13`, source commit
`00fc08302e56756aa995c0fed71e3cdb994f70ce`. The release workflow run is
<https://github.com/upaayan/codex-with-openmodels/actions/runs/32365245182>.
The Mac archive and deployed release hashes are:

| Item | SHA-256 |
|---|---|
| `codex-with-openmodels-aarch64-apple-darwin.tar.gz` | `55e5e8607f2d50ddb7cd88d55b551a3030427ac46bc048ed41d8edc58221ed05` |
| `dist/sudhir-codex-core` | `efc2a6f1984202e8cf530612b15a1301280a84a811f934603ed5b69127c7c412` |
| `dist/codex-code-mode-host` | `632d14d6127f59702bf6a76025e33c8a2013a513d2c021848c1fd417a85fa7d8` |
| `dist/codex-responses-api-proxy` | `7d57373ca9e63c74a1b3da696c725402a8125fc6df18824410791246eaf372ce` |

The durable candidate is stored at:

```text
/Users/sudhirjha/.playground/sudhir-codex/dist/release-evidence/
openmodels-v0.1.0-rc.13/
```

The one retained pre-`rc.13` Mac rollback is stored locally and on the backup
server at:

```text
/Users/sudhirjha/.playground/sudhir-codex/dist/backups/
pre-openmodels-v0.1.0-rc.13-20260820T130328Z/

/home/ubuntu/.sudhir-codex/backend-backups/
pre-openmodels-v0.1.0-rc.13-20260820T130328Z/
```

Both copies pass their `SHA256SUMS` manifest. Older standalone Rust binary
backups were removed after that verification; unrelated configuration,
launcher, gateway, frontend and archived-state material was preserved.

### 4.4 Build once, test after the build

Native Rust candidates are produced by GitHub Actions, not rebuilt locally.
The `native-release` workflow performs inexpensive non-Rust checks first, then
exactly one Cargo build per platform containing every required binary. The
already-built app-server artifact is tested directly afterward. Rust formatting
is also checked after both native build jobs; it does not compile again.

This policy avoids repeating a 45-minute Rust compile for separate tests. A
failed post-build test rejects the candidate without starting another build in
the same run.

### 4.5 The post-update discipline

After every official ChatGPT update:

1. record the new official version, build, signature and Browser client hash;
2. run the transition status and managed-config validator;
3. launch Sudhir-Codex and verify app-server/control health;
4. test one fresh non-GPT draft and one existing-task model change;
5. if successful, create a new dated recovery point and copy it to the backup
   server;
6. if the fresh draft snaps back, inspect the actual picker RPC and response:
   `config/batchWrite` with an unchanged GPT default is an app-server contract
   problem; no update RPC is an official-frontend compatibility problem.

### 4.6 Current platform status

Mac activation and GUI acceptance are complete. In a fresh GPT draft, selecting
`Gemini 3.7 Flash` remained selected after eight seconds and persisted
`model=pi-google-vertex/gemini-3.7-flash` with reasoning effort `high`.

WSL has not been changed or validated yet. Its activation is a separate owner-
approved step using the Linux artifact from the same release candidate.

### 4.7 Gateway source is git-protected, not app-protected

The Sudhir gateway is Python source in `~/.playground/sudhir-codex`, which is
separate from `/Applications/ChatGPT.app`. An official ChatGPT upgrade replaces
only files inside the app bundle and never touches the gateway source. The
Sudhir desktop uses the same gateway on `127.0.0.1:32179`, so any gateway change
is shared by both dsh and the Sudhir app.

Gateway changes are protected by git, not by the launcher. A change is only
durable once it is committed and pushed. Restore a committed gateway change
with:

```bash
git -C ~/.playground/sudhir-codex checkout <commit> -- \
  sudhir_codex/src/sudhir_codex_gateway/<file>
```

Known gateway changes to preserve:

- `b80c38232` — accept chat-style untyped message items from pi-ai (dsh); also
  normalizes string `content`. Fixes `unsupported_input_item` and
  `invalid_content` for non-Cursor open models reached through dsh.
- `9beedfbf6` — accept `Authorization: Bearer` for dsh credential injection.

The objective is not to make updates impossible. It is to make the known
failure classes recognizable and recoverable in minutes, with a clear stop
condition when the official UI itself changed.
