# Sudhir-Codex Rapid Repair Runbook

**Audience:** Luna or another repair operator with local shell access

**Applies to:** The small transition-launcher architecture

**Target:** Diagnose in five minutes; repair known config failures in ten

**Last verified:** 20 August 2026

Read the owner overview first:
[SUDHIR-CODEX-SYSTEM-OVERVIEW.md](SUDHIR-CODEX-SYSTEM-OVERVIEW.md).

## 0. Scope and non-negotiable boundaries

The installed `/Applications/Sudhir-Codex.app` is a small launcher for the
official `/Applications/ChatGPT.app`. This runbook does not authorize a full
Electron clone.

During routine diagnosis and repair:

- do not rebuild Rust locally; use a GitHub Actions native-release artifact
  only after evidence identifies an approved backend change;
- do not rebuild or change the gateway unless evidence reaches that layer;
- do not copy, patch, re-sign or replace the official ChatGPT application;
- do not deploy `patch-frontend-model-drafts.mjs`;
- do not replace all of `~/.sudhir-codex`;
- do not restore a complete `config.toml` merely because a baseline hash
  differs;
- do not stop official ChatGPT or replace an installed app without telling the
  owner exactly what will happen and receiving approval;
- preserve every pre-repair file in a new dated directory.

If a proposed action crosses one of those boundaries, stop and ask the owner.

## 1. Fixed paths

```text
Official UI:          /Applications/ChatGPT.app
Sudhir launcher:      /Applications/Sudhir-Codex.app
Transition command:  ~/.playground/sudhir-codex/scripts/gpt-pro-frontend-transition
Primary state:       ~/.sudhir-codex
Primary config:      ~/.sudhir-codex/config.toml
Model visibility:    ~/.sudhir-codex/model-visibility.json
Global UI state:     ~/.sudhir-codex/.codex-global-state.json
Model cache:         ~/.sudhir-codex/models_cache.json
Control root:        ~/.sudhir-codex/control
Stable node_repl:    ~/.sudhir-codex/control/bin/sudhir-primary-node-repl
Electron profile:    ~/Library/Application Support/Sudhir-Codex-ChatGPT
Launcher log:        ~/Library/Logs/Sudhir-Codex-Launcher.log
Desktop logs:        ~/Library/Logs/com.openai.codex/YYYY/MM/DD/
Gateway log:         ~/.sudhir-codex/gateway/gateway.log
Gateway routes:      ~/.sudhir-codex/gateway/routes.jsonl
Active app-server:   ~/.playground/sudhir-codex/dist/sudhir-codex-core
Active code host:    ~/.playground/sudhir-codex/dist/codex-code-mode-host
Active proxy:        ~/.playground/sudhir-codex/dist/codex-responses-api-proxy
Known-good repair:   ~/.sudhir-codex/config-backups/trust-repair-20260820T035726Z
rc.13 evidence:      ~/.playground/sudhir-codex/dist/release-evidence/openmodels-v0.1.0-rc.13
rc.13 rollback:      ~/.playground/sudhir-codex/dist/backups/pre-openmodels-v0.1.0-rc.13-20260820T130328Z
Backup server:       ubuntu@216.48.177.217
Remote repair root:  /home/ubuntu/.sudhir-codex/config-backups
Remote Rust backup:  /home/ubuntu/.sudhir-codex/backend-backups/pre-openmodels-v0.1.0-rc.13-20260820T130328Z
```

Use absolute paths in repair commands. Do not use a broad recursive copy of
`~/.sudhir-codex` to or from the backup server.

## 2. Five-minute read-only triage

Run these commands from a normal terminal. They do not change the installation.

### 2.1 Record identities and signatures

```bash
/usr/bin/plutil -p /Applications/ChatGPT.app/Contents/Info.plist | \
  /usr/bin/grep -E 'CFBundleIdentifier|CFBundleShortVersionString|CFBundleVersion'
/usr/bin/plutil -p /Applications/Sudhir-Codex.app/Contents/Info.plist | \
  /usr/bin/grep -E 'CFBundleIdentifier|CFBundleShortVersionString|CFBundleVersion'
/usr/bin/codesign --verify --deep --strict /Applications/ChatGPT.app
/usr/bin/codesign --verify --deep --strict /Applications/Sudhir-Codex.app
```

Expected bundle IDs:

```text
Official ChatGPT: com.openai.codex
Sudhir launcher:  com.sudhir.codex
```

The command below is useful for identity and signature fields, but its
`status=update-available` result is not meaningful as an update instruction:
it compares official ChatGPT's build with the small launcher's build.

```bash
/Users/sudhirjha/.playground/sudhir-codex-app/scripts/frontend-status
```

### 2.2 Check the transition and processes

```bash
/Users/sudhirjha/.playground/sudhir-codex/scripts/gpt-pro-frontend-transition status
/bin/ps -axo pid,ppid,lstart,command | \
  /usr/bin/grep -E '/Applications/ChatGPT.app/Contents/MacOS/ChatGPT|sudhir-codex-core.*app-server' | \
  /usr/bin/grep -v grep
```

Healthy runtime essentials are:

```text
running=true
appServerRunning=true
controlRuntime.ready=true
```

Do not treat `primaryConfigUnchanged=false` or
`chromeManifestMatchesSnapshot=false` as an automatic restoration order. They
compare against older snapshots and require a semantic investigation.

### 2.3 Read the failure logs

```bash
/usr/bin/tail -n 120 /Users/sudhirjha/Library/Logs/Sudhir-Codex-Launcher.log
latest_desktop_log="$(/usr/bin/find /Users/sudhirjha/Library/Logs/com.openai.codex \
  -type f -name 'codex-desktop-*.log' \
  -exec /usr/bin/stat -f '%m %N' {} + 2>/dev/null | \
  /usr/bin/sort -nr | /usr/bin/head -n 1 | /usr/bin/cut -d ' ' -f 2-)"
if [ -n "$latest_desktop_log" ]; then
  /usr/bin/tail -n 160 "$latest_desktop_log"
fi
/usr/bin/tail -n 120 /Users/sudhirjha/.sudhir-codex/gateway/gateway.log
```

Do not paste the complete config or environment into a public log. They may
contain provider credentials.

### 2.4 Validate only the managed trust contract

```bash
python3 - <<'PY'
import hashlib
import tomllib
from pathlib import Path

config_path = Path('/Users/sudhirjha/.sudhir-codex/config.toml')
browser_path = Path(
    '/Applications/ChatGPT.app/Contents/Resources/plugins/'
    'openai-bundled/plugins/chrome/scripts/browser-client.mjs'
)
config = tomllib.loads(config_path.read_text(encoding='utf-8'))
expected = hashlib.sha256(browser_path.read_bytes()).hexdigest()

# Build 6849+ rewrites config.toml a few seconds after launch and replaces
# mcp_servers.node_repl.env with its own block that omits the browser hash.
# The launcher recreates both trust entries at the next launch, so:
#   - shell_environment_policy.set is authoritative and must contain the hash;
#   - mcp_servers.node_repl.env must keep the NODE_REPL_TRUSTED_CODE_PATHS
#     anchor, but its browser-hash key may be absent on disk after launch.
shell_policy = config.get('shell_environment_policy', {}).get('set', {})
shell_hashes = str(shell_policy.get('NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S', '')).split(',')
print(f'shell_environment_policy.set: {"PASS" if expected in shell_hashes else "FAIL"}')
failed = expected not in shell_hashes

node_env = config.get('mcp_servers', {}).get('node_repl', {}).get('env', {})
anchor_ok = bool(node_env.get('NODE_REPL_TRUSTED_CODE_PATHS'))
print(f'mcp_servers.node_repl.env anchor: {"PASS" if anchor_ok else "FAIL"}')
failed = failed or not anchor_ok
node_hashes = str(node_env.get('NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S', '')).split(',')
if expected not in node_hashes:
    print('mcp_servers.node_repl.env hash: EXPECTED-ABSENT (frontend rewrote it; launcher recreates at launch)')

command = config.get('mcp_servers', {}).get('node_repl', {}).get('command')
shim = '/Users/sudhirjha/.sudhir-codex/control/bin/sudhir-primary-node-repl'
shim_ok = command == shim and Path(shim).is_file()
print(f'stable node_repl shim: {"PASS" if shim_ok else "FAIL"}')
failed = failed or not shim_ok
raise SystemExit(1 if failed else 0)
PY
```

At the 20 August recovery point, the official Browser client SHA-256 was:

```text
3b9d8dcc6dc968887e8a969c63dae6380e3c1c59ff5c474eb32df08c353dad87
```

Do not hard-code that value after an official ChatGPT update. The validation
script derives the current value from the signed installed official app.

For official ChatGPT `26.818.21641` build `6849`, the current Browser client
SHA-256 is:

```text
53484b46feddd277e436a0c3f38820eca8aab4e32c01bb44e1b5766eb369b5e6
```

Same rule: do not hard-code it. The validator derives it from the installed
official app.

## 3. Choose the symptom before repairing

| Symptom | Go to |
|---|---|
| Launcher reports a missing `NODE_REPL_*` setting | Section 4 |
| App starts but expected models do not appear | Section 5 |
| Models appear, but a fresh draft snaps back to GPT | Section 6 |
| Existing-task model change fails | Section 7 |
| App-server or gateway is not running | Section 8 |
| Failure began immediately after an official update | Section 9, then the matching symptom section |

Do not combine repair branches. Finish diagnosis of one layer before touching
another.

## 4. Repair a missing trust/config entry

This branch covers errors such as:

```text
Missing required setting
mcp_servers.node_repl.env.NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S
```

On the build-6720-era launcher, this error meant the frontend had removed the
managed hash. The current launcher (commit `bb31f11b0`) recreates the hash
after `NODE_REPL_TRUSTED_CODE_PATHS` at every launch instead of hard-failing.
If this error still appears, the deployed launcher predates that commit or the
frontend also removed the anchor; record the official build and the exact
error, then ask the owner before changing the launcher.

### 4.1 Preserve the live preimage

```bash
repair_stamp="$(/bin/date -u +%Y%m%dT%H%M%SZ)"
repair_dir="/Users/sudhirjha/.sudhir-codex/config-backups/trust-repair-${repair_stamp}"
/bin/mkdir -m 700 "$repair_dir"
/bin/cp -p /Users/sudhirjha/.sudhir-codex/config.toml \
  "$repair_dir/config.toml.before"
/bin/chmod 600 "$repair_dir/config.toml.before"
```

Keep the exact `repair_dir` value for the remaining steps.

### 4.2 Run the supported repair path

The current transition code derives the Browser hash from the signed official
app and can reinsert the missing MCP trust entry when its trusted-code anchor is
still present.

The following command may stop a conflicting default-profile official ChatGPT
process and launch the Sudhir transition instance. Tell the owner before running
it if official ChatGPT is open for separate work.

```bash
/Users/sudhirjha/.playground/sudhir-codex/scripts/gpt-pro-frontend-transition \
  ensure-primary
```

If it succeeds, copy the repaired file into the recovery directory:

```bash
/bin/cp -p /Users/sudhirjha/.sudhir-codex/config.toml \
  "$repair_dir/config.toml.repaired-final"
/bin/chmod 600 "$repair_dir/config.toml.repaired-final"
```

Run the validator in section 2.4 and the runtime checks in section 10.

If `ensure-primary` still reports a missing anchor or duplicate key, do not
replace the whole config. Go to section 4.4.

### 4.3 Exact restoration from the 20 August recovery point

Use this only when the live file is byte-for-byte the recorded 20 August
preimage. This condition prevents a stale full-config restore from deleting
newer settings.

```bash
known_dir=/Users/sudhirjha/.sudhir-codex/config-backups/trust-repair-20260820T035726Z
live_hash="$(/usr/bin/shasum -a 256 /Users/sudhirjha/.sudhir-codex/config.toml | \
  /usr/bin/awk '{print $1}')"
test "$live_hash" = b6e6557b1c898b8385a13c83718f6e8fc3bd9129643800b6a33ca8347479a045
/bin/cp -p /Users/sudhirjha/.sudhir-codex/config.toml \
  "/Users/sudhirjha/.sudhir-codex/config-backups/config.toml.pre-exact-restore.$(/bin/date -u +%Y%m%dT%H%M%SZ)"
restore_tmp="$(/usr/bin/mktemp /Users/sudhirjha/.sudhir-codex/.config.toml.restore.XXXXXX)"
/usr/bin/install -m 600 "$known_dir/config.toml.repaired-final" "$restore_tmp"
/bin/mv "$restore_tmp" /Users/sudhirjha/.sudhir-codex/config.toml
```

Then open the launcher and validate:

```bash
/usr/bin/open /Applications/Sudhir-Codex.app
```

Do not use this exact restore if the `test` command fails.

### 4.4 Stop condition for a non-matching live config

If the supported repair fails and the live config does not match the known
preimage:

1. keep the new `config.toml.before` copy;
2. compare table/key names without printing secret values;
3. report the missing or duplicate keys and the official version/build;
4. ask the owner to approve a selective config repair.

Do not paste the old full config over the new one. The
`managed-trust-entries.toml` file is a reference fragment, not a complete
configuration, and its hash is version-specific.

### 4.5 Create and verify the local recovery point

After validation, create a small current-version fragment without exposing
other config values:

```bash
python3 - "$repair_dir/managed-trust-entries.toml" <<'PY'
import tomllib
from pathlib import Path
import sys

source = Path('/Users/sudhirjha/.sudhir-codex/config.toml')
destination = Path(sys.argv[1])
config = tomllib.loads(source.read_text(encoding='utf-8'))
tables = [
    ('shell_environment_policy.set',
     config['shell_environment_policy']['set']),
    ('mcp_servers.node_repl.env',
     config['mcp_servers']['node_repl']['env']),
]
keys = (
    'NODE_REPL_TRUSTED_CODE_PATHS',
    'NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S',
)
lines = []
for name, table in tables:
    lines.append(f'[{name}]')
    for key in keys:
        value = str(table[key]).replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'{key} = "{value}"')
    lines.append('')
destination.write_text('\n'.join(lines), encoding='utf-8')
destination.chmod(0o600)
PY

( cd "$repair_dir" && /usr/bin/shasum -a 256 \
  config.toml.before config.toml.repaired-final managed-trust-entries.toml \
  > SHA256SUMS )
/bin/chmod 600 "$repair_dir/SHA256SUMS"
( cd "$repair_dir" && /usr/bin/shasum -a 256 -c SHA256SUMS )
```

### 4.6 Copy the recovery point to the backup server

Use a never-overwrite remote directory. Replace the example stamp with the
stamp from section 4.1.

```bash
remote_root=/home/ubuntu/.sudhir-codex/config-backups
remote_name="trust-repair-${repair_stamp}"
remote_dir="$remote_root/$remote_name"
remote_incoming="$remote_root/.${remote_name}.incoming"
ssh -o BatchMode=yes ubuntu@216.48.177.217 \
  "test ! -e '$remote_dir' && test ! -e '$remote_incoming' && mkdir -m 700 -p '$remote_incoming'"
rsync -a --partial "$repair_dir/" \
  "ubuntu@216.48.177.217:$remote_incoming/"
ssh -o BatchMode=yes ubuntu@216.48.177.217 \
  "find '$remote_incoming' -type f -exec chmod 600 {} + && cd '$remote_incoming' && sha256sum -c SHA256SUMS && mv '$remote_incoming' '$remote_dir'"
```

If transfer is interrupted, rerun only the `rsync` and final `ssh` commands;
`--partial` resumes the incomplete copy. If the final remote directory already
exists, stop rather than overwrite it.

The currently verified local and remote set is
`trust-repair-20260820T035726Z`; all four original files match byte-for-byte on
both machines.

## 5. Models are missing from the list

This is a catalog/visibility branch, not a draft-persistence branch.

### 5.1 Validate the state files without changing them

```bash
python3 -m json.tool /Users/sudhirjha/.sudhir-codex/model-visibility.json \
  >/dev/null
python3 -m json.tool /Users/sudhirjha/.sudhir-codex/.codex-global-state.json \
  >/dev/null
python3 -m json.tool /Users/sudhirjha/.sudhir-codex/models_cache.json \
  >/dev/null
/bin/ls -l /Users/sudhirjha/.sudhir-codex/model-visibility.json \
  /Users/sudhirjha/.sudhir-codex/.codex-global-state.json \
  /Users/sudhirjha/.sudhir-codex/models_cache.json
```

Inspect the policy structure, not credentials or complete state:

```bash
jq '{default, show, hide}' \
  /Users/sudhirjha/.sudhir-codex/model-visibility.json
```

Then inspect recent gateway routes and desktop catalog errors. If the model is
absent from the catalog, investigate the gateway/catalog source. If it is in
the catalog but excluded by visibility policy, repair only
`model-visibility.json` from an owner-approved known-good copy.

There is not yet a dedicated current-version backup of
`model-visibility.json` in the 20 August trust-repair set. Do not use an old
archived transition copy without owner approval.

## 6. Fresh new-task model selection snaps back to GPT

### 6.1 Reproduce the two cases

1. Open a completely fresh, unsent task.
2. Select a non-GPT model and wait five seconds.
3. Note whether it snaps back before sending.
4. Open an existing task and select the same model.
5. Note whether the existing task retains it.

### 6.2 Inspect the desktop RPC evidence

Locate the newest desktop log as in section 2.3, then run:

```bash
/usr/bin/grep -E \
  'method=(config/read|config/batchWrite|thread/settings/update)|Setting default model' \
  "$latest_desktop_log" | /usr/bin/tail -n 120
```

Interpretation:

- Fresh draft sends `config/batchWrite` for `model` and
  `model_reasoning_effort`, app-server reports an overridden result, and
  `config/read` still returns GPT: pre-`rc.13` app-server policy regression.
- Fresh draft snaps back and no model update reaches app-server: official
  frontend new-task state regression. The `rc.13` backend repair does not cover
  this different failure.
- Existing task emits `thread/settings/update` and succeeds: backend, gateway,
  provider registration and model visibility work for existing tasks. It does
  not prove that new-task default persistence works.
- An update request reaches app-server and receives an error: continue to
  section 7.

### 6.3 Known build-6720 backend repair

Official ChatGPT build `6720` sends fresh-draft selection through
`config/batchWrite`. The old Sudhir app-server intentionally discarded those
two keys and returned `okOverridden`; the frontend then reread the GPT default
and snapped back. `openmodels-v0.1.0-rc.13` accepts and persists the two edits,
while existing-task selection remains thread-scoped through
`thread/settings/update`.

The approved source and build evidence are:

```text
Tag:       openmodels-v0.1.0-rc.13
Commit:    00fc08302e56756aa995c0fed71e3cdb994f70ce
CI run:    https://github.com/upaayan/codex-with-openmodels/actions/runs/32365245182
Mac SHA:   55e5e8607f2d50ddb7cd88d55b551a3030427ac46bc048ed41d8edc58221ed05
```

GitHub Actions performs inexpensive non-Rust checks first. Each platform then
has exactly one Cargo build containing all required native binaries. The JSON-
RPC artifact test and Rust checks run only after the build; do not launch a
second local or CI Rust build to validate this candidate.

### 6.4 Verify the saved Mac candidate without compiling

```bash
candidate=/Users/sudhirjha/.playground/sudhir-codex/dist/release-evidence/openmodels-v0.1.0-rc.13
( cd "$candidate/assets" && \
  /usr/bin/shasum -a 256 -c SHA256SUMS --ignore-missing )
( cd "$candidate" && /usr/bin/shasum -a 256 -c staged-binary-SHA256SUMS )
for binary in \
  "$candidate/macos-stage/codex" \
  "$candidate/macos-stage/codex-code-mode-host" \
  "$candidate/macos-stage/codex-responses-api-proxy"
do
  /usr/bin/file "$binary"
  /usr/bin/codesign --verify --verbose=2 "$binary"
done
git show \
  00fc08302e56756aa995c0fed71e3cdb994f70ce:scripts/tests/sudhir_app_server_artifact.py | \
  python3 - "$candidate/macos-stage/codex"
```

Expected: the archive and all three staged hashes pass, every binary is Mach-O
`arm64`, every signature verifies, and the last command prints
`verified model-picker persistence`.

### 6.5 Preserve the current release before activation

Create a never-overwrite directory. This example is the retained rollback from
the verified 20 August activation; use a new UTC stamp for a later release.

```bash
backup=/Users/sudhirjha/.playground/sudhir-codex/dist/backups/pre-openmodels-v0.1.0-rc.13-20260820T130328Z
test ! -e "$backup"
/bin/mkdir -m 700 "$backup"
/bin/cp -p /Users/sudhirjha/.playground/sudhir-codex/dist/sudhir-codex-core \
  "$backup/sudhir-codex-core"
/bin/cp -p /Users/sudhirjha/.playground/sudhir-codex/dist/codex-code-mode-host \
  "$backup/codex-code-mode-host"
/bin/cp -p /Users/sudhirjha/.playground/sudhir-codex/dist/codex-responses-api-proxy \
  "$backup/codex-responses-api-proxy"
( cd "$backup" && /usr/bin/shasum -a 256 \
  sudhir-codex-core codex-code-mode-host codex-responses-api-proxy \
  > SHA256SUMS )
( cd "$backup" && /usr/bin/shasum -a 256 -c SHA256SUMS )
```

Copy it to a new server directory and verify it there before activation:

```bash
remote_root=/home/ubuntu/.sudhir-codex/backend-backups
remote_name=pre-openmodels-v0.1.0-rc.13-20260820T130328Z
remote="$remote_root/$remote_name"
incoming="$remote_root/.${remote_name}.incoming"
ssh -o BatchMode=yes ubuntu@216.48.177.217 \
  "test ! -e '$remote' && test ! -e '$incoming' && mkdir -m 700 -p '$incoming'"
rsync -a --partial "$backup/" "ubuntu@216.48.177.217:$incoming/"
ssh -o BatchMode=yes ubuntu@216.48.177.217 \
  "find '$incoming' -type f -exec chmod 600 {} + && cd '$incoming' && sha256sum -c SHA256SUMS && mv '$incoming' '$remote'"
```

If transfer is interrupted, rerun only the `rsync` and final `ssh` commands;
`--partial` resumes the incomplete copy. If the final server directory already
exists, stop. Do not overwrite it.

### 6.6 Activate atomically and restart the Sudhir instance

Tell the owner, close the Sudhir-Codex UI, and confirm that the isolated
`Sudhir-Codex-ChatGPT` process and its app-server have stopped. Then install all
three files through temporary siblings so each final rename is atomic:

```bash
candidate=/Users/sudhirjha/.playground/sudhir-codex/dist/release-evidence/openmodels-v0.1.0-rc.13/macos-stage
dist=/Users/sudhirjha/.playground/sudhir-codex/dist
for pair in \
  "codex:sudhir-codex-core" \
  "codex-code-mode-host:codex-code-mode-host" \
  "codex-responses-api-proxy:codex-responses-api-proxy"
do
  source_name="${pair%%:*}"
  target_name="${pair##*:}"
  temporary="$(/usr/bin/mktemp "$dist/.${target_name}.rc13.XXXXXX")"
  /usr/bin/install -m 755 "$candidate/$source_name" "$temporary"
  /bin/mv "$temporary" "$dist/$target_name"
done
/usr/bin/open /Applications/Sudhir-Codex.app
```

Run the transition status and hash commands in section 10, then perform the
real GUI acceptance: open a fresh GPT draft, select one non-GPT model, wait at
least eight seconds, and confirm both the retained picker label and the
`model`/`model_reasoning_effort` values in `config.toml`.

The 20 August Mac acceptance selected `Gemini 3.7 Flash`; it remained selected
after eight seconds and persisted `pi-google-vertex/gemini-3.7-flash` with
reasoning effort `high`.

### 6.7 Atomic rollback if acceptance fails

Close the Sudhir instance again. Verify the retained rollback before replacing
anything:

```bash
backup=/Users/sudhirjha/.playground/sudhir-codex/dist/backups/pre-openmodels-v0.1.0-rc.13-20260820T130328Z
dist=/Users/sudhirjha/.playground/sudhir-codex/dist
( cd "$backup" && /usr/bin/shasum -a 256 -c SHA256SUMS )
for name in sudhir-codex-core codex-code-mode-host codex-responses-api-proxy
do
  temporary="$(/usr/bin/mktemp "$dist/.${name}.rollback.XXXXXX")"
  /usr/bin/install -m 755 "$backup/$name" "$temporary"
  /bin/mv "$temporary" "$dist/$name"
done
/usr/bin/open /Applications/Sudhir-Codex.app
```

Validate startup and the original known-working behaviour. Do not delete the
rollback after a failed activation.

### 6.8 Hard stop for a true frontend-only regression

If the picker sends no update RPC:

- do not activate another Rust build;
- do not rebuild the gateway;
- do not edit `model-visibility.json`;
- do not replace `.codex-global-state.json` as a speculative repair;
- do not deploy the full-clone ASAR patch.

Record the official version/build and the relevant log excerpt. Report:

```text
The model is present. Existing-task switching works. The fresh draft sent no
model update to app-server, so the selection was reverted inside the official
frontend before the backend could act.
```

The current small-launcher architecture has no installed mechanism that patches
official frontend JavaScript. This different failure therefore requires a
separately designed and owner-approved launcher-compatible remedy, or an
official upstream fix. The existing source-only ASAR patch is un-deployed
full-clone research and is not authorized.

## 7. Existing-task model selection fails

If an existing task emits `thread/settings/update`, capture the response/error
and inspect the corresponding gateway route:

```bash
/usr/bin/tail -n 200 /Users/sudhirjha/.sudhir-codex/gateway/routes.jsonl
/usr/bin/tail -n 200 /Users/sudhirjha/.sudhir-codex/gateway/gateway.log
```

Decision:

- no request left the frontend: frontend problem;
- app-server rejected the request before a provider route: app-server/config
  problem;
- gateway received the route and returned an error: gateway/provider problem;
- provider accepted the route but the UI reverted: inspect response handling.

Only after the failing layer is established should source or tests be touched.
If a gateway source change is actually required, run only the named focused
gateway regression tests. If Rust source is unchanged, do not build Rust.

## 8. App-server or gateway is not running

### 8.1 Verify required executables and state

```bash
test -x /Users/sudhirjha/.playground/sudhir-codex/scripts/gpt-pro-frontend-transition
test -x /Users/sudhirjha/.local/bin/sudhir-codex
test -x /Users/sudhirjha/.playground/sudhir-codex/dist/sudhir-codex-core
test -x /Users/sudhirjha/.sudhir-codex/control/bin/sudhir-primary-node-repl
test -f /Users/sudhirjha/.sudhir-codex/config.toml
```

If a required file is missing, report that exact file. Do not rebuild a
different component. The active native helpers are the copies under `dist/`,
not a similarly named stale copy under a plugin cache.

### 8.2 Relaunch through the supported entrypoint

After preserving the launcher log and confirming that a separate official
ChatGPT task will not be unexpectedly stopped:

```bash
/usr/bin/open /Applications/Sudhir-Codex.app
```

Re-run transition status. If startup fails, the launcher's stderr and the
desktop log identify whether the failure occurred before app-server launch,
during the app-server handshake, or in the gateway.

## 9. Official ChatGPT update procedure

An official update is a compatibility event, not authorization for a new app
build.

### 9.1 Record the new official identity and Browser hash

```bash
/usr/bin/plutil -p /Applications/ChatGPT.app/Contents/Info.plist | \
  /usr/bin/grep -E 'CFBundleIdentifier|CFBundleShortVersionString|CFBundleVersion'
/usr/bin/codesign --verify --deep --strict /Applications/ChatGPT.app
/usr/bin/shasum -a 256 \
  /Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/plugins/chrome/scripts/browser-client.mjs
```

### 9.2 Validate in this order

1. Run the trust validator in section 2.4.
2. Start Sudhir-Codex through the small launcher.
3. Require `running`, `appServerRunning` and `controlRuntime.ready` to be true.
4. Test a fresh non-GPT draft.
5. Test a model change in an existing task.
6. Test Browser/Computer Use only if the update changed those bundled resources
   or that capability is needed.
7. If all pass, create a new recovery point and copy it to the backup server.

If the trust validator fails, use section 4. If only the fresh draft fails and
no update request reaches app-server, use section 6.8. If build `6720` sends
`config/batchWrite` but the old GPT default is returned, use the verified
`rc.13` path in sections 6.3-6.7. Do not run a Rust build as an update ritual.

### 9.3 Build 6849 config-rewrite behavior

Official ChatGPT `26.818.21641` build `6849` rewrites
`~/.sudhir-codex/config.toml` a few seconds after launch. Its new
`mcp_servers.node_repl.env` block keeps `NODE_REPL_TRUSTED_CODE_PATHS` and adds
build-specific keys such as `BROWSER_USE_AVAILABLE_BACKENDS` and
`NODE_REPL_TRUSTED_SERVICES`, but omits
`NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S`. It leaves
`shell_environment_policy.set.NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S` at the
current hash.

Expected consequences:

- the on-disk `mcp_servers.node_repl.env` browser-hash key may be absent after
  every fresh launch even though the app is healthy;
- the launcher recreates both trust entries at the next launch (commit
  `bb31f11b0`), so startup keeps working;
- treat `shell_environment_policy.set` as the authoritative hash table.

If a later official build also removes the `NODE_REPL_TRUSTED_CODE_PATHS`
anchor or renames the trust contract, the launcher will fail again with a
`Cannot insert` or `Missing required setting` error. Record the official
version/build and the exact error, then ask the owner before changing the
launcher.

## 10. Final validation checklist

Do not declare a repair complete until all items relevant to the symptom pass.

### 10.1 Startup/config repair

- Both app signatures are valid.
- Bundle IDs are `com.openai.codex` and `com.sudhir.codex`.
- `shell_environment_policy.set` contains the current official Browser hash
  (authoritative for build 6849+).
- `mcp_servers.node_repl.env` either contains the current hash or keeps the
  `NODE_REPL_TRUSTED_CODE_PATHS` anchor; an absent hash on disk is expected
  because the frontend rewrites that block after launch.
- `mcp_servers.node_repl.command` selects
  `~/.sudhir-codex/control/bin/sudhir-primary-node-repl`.
- Transition status reports `running=true`.
- Transition status reports `appServerRunning=true`.
- Transition status reports `controlRuntime.ready=true`.
- The launcher log has no new `Missing required setting` failure.
- The local recovery directory has a passing `SHA256SUMS`.
- The remote recovery directory passes `sha256sum -c SHA256SUMS`.

### 10.2 Model repair/diagnosis

- Expected models are present in the catalog.
- Visibility policy matches the owner's requested shown/hidden set.
- A fresh draft either retains the non-GPT selection or is explicitly recorded
  as the known official-frontend boundary.
- An existing task's model change emits `thread/settings/update`.
- The gateway route uses the selected provider/model when a turn is sent.
- No unrelated global default was changed as a workaround.

### 10.3 `rc.13` Mac backend

```bash
/usr/bin/shasum -a 256 \
  /Users/sudhirjha/.playground/sudhir-codex/dist/sudhir-codex-core \
  /Users/sudhirjha/.playground/sudhir-codex/dist/codex-code-mode-host \
  /Users/sudhirjha/.playground/sudhir-codex/dist/codex-responses-api-proxy
( cd /Users/sudhirjha/.playground/sudhir-codex/dist/backups/pre-openmodels-v0.1.0-rc.13-20260820T130328Z && \
  /usr/bin/shasum -a 256 -c SHA256SUMS )
ssh -o BatchMode=yes ubuntu@216.48.177.217 \
  "cd /home/ubuntu/.sudhir-codex/backend-backups/pre-openmodels-v0.1.0-rc.13-20260820T130328Z && sha256sum -c SHA256SUMS"
```

Expected active hashes, in order:

```text
efc2a6f1984202e8cf530612b15a1301280a84a811f934603ed5b69127c7c412
632d14d6127f59702bf6a76025e33c8a2013a513d2c021848c1fd417a85fa7d8
7d57373ca9e63c74a1b3da696c725402a8125fc6df18824410791246eaf372ce
```

Mac is verified. WSL is explicitly pending and must not be changed until the
owner starts that separate phase.

## 11. Evidence to report to the owner

Give a concise report containing:

1. exact symptom;
2. official ChatGPT version/build;
3. failing layer and the evidence that locates it;
4. files changed, if any;
5. pre-repair and repaired hashes;
6. local backup path;
7. remote backup path and checksum result;
8. final runtime and GUI validation results;
9. anything still unresolved.

Never say a frontend problem is fixed merely because config validation passed.
Never say the installation is healthy merely because the launcher opened. Test
the real behaviour that originally failed.
