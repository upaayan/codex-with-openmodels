# Sudhir-Codex CLI and app backend

Sudhir-Codex is an independent build of the open-source Codex CLI with one
private loopback gateway. The gateway presents Codex-subscription GPT models
and compatible non-Codex models in Pi's registry, plus four Cursor Composer
routes, as one model catalog.

## What is independent

- Source and build: `<checkout>`
- Installed Unix launcher copy: `$HOME/.local/bin/sudhir-codex`
- Codex state and copied OAuth credentials: `$HOME/.sudhir-codex`
- Python environment: `<checkout>/.venv`
- Pinned Cursor SDK worker: `<checkout>/sudhir_codex/cursor_worker`
- Cursor worker state: `$HOME/.sudhir-codex/cursor-sdk`
- Fork-local Rust toolchain for Unix source builds: `<checkout>/.toolchains`
- Core executable: `<checkout>/dist/sudhir-codex-core` on Unix or
  `<checkout>\dist\sudhir-codex-core.exe` on Windows

There are no symlinks to the official Codex installation. The core executable
also refuses to run with `CODEX_HOME=~/.codex`, a relative home, an overlapping
home, or a symlinked home.

Pi's `$HOME/.pi/agent/models.json` and `auth.json` remain Pi-owned, read-only
inputs. Sudhir-Codex does not modify or copy them. The Cursor worker reads only
the `cursor` API-key entry from Pi auth; its SDK, processes, and state are
otherwise independent of Pi.

## Installation

On Apple Silicon macOS or Ubuntu 22.04 x64, clone the exact release tag and
download its matching archive plus `SHA256SUMS`. Install the already-built
release without compiling Rust:

```bash
cd /path/to/codex-with-openmodels
./scripts/install-sudhir-codex \
  --archive ./codex-with-openmodels-aarch64-apple-darwin.tar.gz \
  --checksums ./SHA256SUMS
```

On Ubuntu x64, use
`codex-with-openmodels-x86_64-unknown-linux-musl.tar.gz` instead. This mode
verifies the checksum and exact archive contents, installs the CLI and helper
layout into `dist`, and retains an existing runtime as `.previous`. The Linux
bundle installs `bwrap` under `dist/codex-resources/`.

If the system `python3` is older than 3.11 but a newer interpreter is already
installed, set `SUDHIR_CODEX_PYTHON` to its executable path when running the
installer.

The source-build mode remains available:

```bash
cd /path/to/codex-with-openmodels
./scripts/install-sudhir-codex
```

In source-build mode the installer downloads Rust 1.95 into the fork and builds
the CLI. Both modes install the exactly locked Cursor SDK with `npm ci`, create
the private Python environment, copy the launcher, initialize private state,
copy official Codex auth only when private auth does not exist, and import only
the official config's `[mcp_servers.*]` tables.

On native Windows x64, clone the exact release tag, download the matching ZIP
and `SHA256SUMS`, then run:

```powershell
.\scripts\sudhir-codex.ps1 `
  -Archive .\codex-with-openmodels-x86_64-pc-windows-msvc.zip `
  -Checksums .\SHA256SUMS
```

The PowerShell installer verifies the archive, installs the prebuilt binaries,
creates the private Python environment, runs the pinned Cursor-worker install,
initializes independent state, and writes only
`<checkout>\bin\sudhir-codex.cmd`. Use `-ImportOfficialState` only when you
want to copy official auth and MCP configuration into the independent state.

It never installs or replaces a command named `codex`.

## Model catalog and selection

List the merged catalog:

```bash
sudhir-codex models
sudhir-codex models --json
```

### Picker visibility

`$HOME/.sudhir-codex/model-visibility.json` controls which merged GPT and open
models appear in `/model` and in the subagent model picker. It is read whenever
the gateway serves `/models`, so changing it does not require a Rust rebuild.
The underlying routes remain available by exact model ID even when hidden.

The policy accepts `default` (`list` or `hide`) plus `show` and `hide` arrays of
case-sensitive shell-style glob patterns. `hide` wins if both arrays match.
Start a new CLI session, or otherwise refresh its model catalog, after changing
the file.

GPT model IDs retain their official names. Pi model IDs are deterministic:

```text
pi-<pi-provider>/<original-model-id>
```

Examples:

```bash
sudhir-codex -m gpt-5.6-sol
sudhir-codex -m pi-zai/glm-5.2
sudhir-codex exec -m pi-deepseek/deepseek-chat "Inspect this repository."
sudhir-codex exec -m cursor/composer-latest-fast "Inspect this repository."
```

The deliberately exposed Cursor IDs are:

```text
cursor/composer-2.5-fast
cursor/composer-2.5-slow
cursor/composer-latest-fast
cursor/composer-latest-slow
```

The `composer-2.5` routes stay pinned. Cursor resolves `composer-latest`
dynamically, so those two routes can move to Composer 3 when Cursor changes the
alias. Fast/slow is part of the model ID; the displayed High effort is a fixed
Codex metadata value, not a second Cursor reasoning control.

Inside the TUI, `/model` shows the same merged catalog. Selecting another model
changes the model for subsequent turns in that task. Different tasks can use
different models at the same time.

Each task still has one selected model per turn; this is not an ensemble that
sends one turn to several providers.

## Tools, MCP clients, and agents

For GPT and Pi models, Codex remains responsible for shell, patch, local
dynamic tools, skills, and MCP execution. The gateway translates the tool
schemas and model-emitted calls for Pi models, including namespaced MCP and
multi-agent calls.

Composer runs through Cursor's native local-agent loop. Each turn receives the
complete Codex task transcript and runs in a short-lived Node process whose
actual OS working directory is the verified task repo. Cursor therefore owns
Composer's shell/read/edit loop. It loads Cursor's ambient
`settingSources=["all"]`, including Cursor-native settings, rules, skills, and
MCP configuration. Codex MCP tables in `$HOME/.sudhir-codex/config.toml` are not
automatically translated into Cursor SDK MCP definitions.

The per-turn process boundary is intentional. Cursor's shell inherits the Node
process directory even when the SDK receives a separate `local.cwd`. Launching
inside the task repo prevents tools from starting in the gateway source
directory and lets six Codex child tasks run concurrently, including across
different repositories.

Cross-provider agent tools use the fork-specific `sudhir_agents` namespace.
Their task and message payloads are ordinary plaintext model input carried over
the same local/HTTPS routes as prompts. The upstream `collaboration` namespace
uses ChatGPT-only encrypted payloads that Pi endpoints cannot decrypt, so it is
not used by this fork.

Refresh the private copy of official MCP definitions after changing the
official config:

```bash
sudhir-codex mcp import
```

Picker-visible models in the merged catalog are eligible as exact subagent
model overrides, even when they are not among the five examples shown in the
tool description. The private config allows six concurrently running agents.
A prompt can therefore say:

```text
Use six agents. Use model pi-zai/glm-5.2 for each agent.
```

The same mechanism accepts an exact Cursor ID. Each child Codex task launches
its own Composer native-agent turn.

The model still decides when and how to invoke the agent tools. A provider must
actually support ordinary OpenAI-style function calls for its model to drive a
tool loop.

Hosted ChatGPT web search remains available to GPT models. Pi models do not
receive the provider-hosted web-search tool; when Codex has a standalone
`web.run` extension, that normal client-executed tool can still be used.

## Exposing Sudhir-Codex as an MCP server

The fork retains the upstream MCP server:

```bash
sudhir-codex mcp-server
```

An MCP client can register it independently:

```toml
[mcp_servers.sudhir_codex]
command = "/path/to/sudhir-codex"
args = ["mcp-server"]
```

The MCP `codex` tool accepts its normal `model` override. Use any exact ID from
`sudhir-codex models`, including a GPT, `pi-...`, or `cursor/...` ID.
MCP-created Codex tasks use the same private home, merged catalog, gateway, and
`sudhir_agents` namespace.

## Authentication

The initial Unix install copies `$HOME/.codex/auth.json` to
`$HOME/.sudhir-codex/auth.json` with mode `0600`. On Windows, the private files
receive a current-user ACL. It is a copy, not a link. Thereafter the two
installations refresh and update their own files.

To replace private auth with a fresh copy of official auth:

```bash
sudhir-codex auth import
```

An existing private file is backed up first. Alternatively,
`sudhir-codex login` performs a login directly in the private Codex home.

The OAuth server controls access-token and refresh-token lifetimes. This fork
does not and cannot extend them.

## Gateway lifecycle and diagnostics

```bash
sudhir-codex gateway status
sudhir-codex gateway stop
sudhir-codex gateway start
sudhir-codex gateway rotate-token
sudhir-codex doctor
sudhir-codex doctor --json
```

The gateway binds only to `127.0.0.1:32179`. Every route requires a random
private client token. Redirects are disabled for credentialed upstream
requests. ChatGPT credentials go only to the fixed ChatGPT Codex origin; each
Pi endpoint receives only its own resolved credential.

The launcher keeps the gateway token in the parent Codex process for provider
authentication but forcibly excludes it from model-invoked shell tools. New
shell snapshots omit the token, and stale snapshots cannot restore it.

The local route journal records timestamp, model ID, provider ID, destination
hostname, status, and duration. It does not record prompts, tool data, response
bodies, or credentials.

## Telemetry

The launcher forcibly disables optional Codex analytics delivery, feedback,
OTEL logs, OTEL traces, OTEL metrics, startup update checks, and user-prompt
OTEL logging. Request compression is also disabled because the mixed gateway
must inspect routing metadata.

Normal model traffic is not Codex telemetry: GPT prompts and tool schemas
necessarily go to the ChatGPT Codex backend, Pi-model prompts and tool schemas
go to the selected Pi endpoint, and Composer transcripts go to Cursor through
its SDK. Those providers may have their own service-side logging policies.

## Current adapter limits

- Pi providers must expose OpenAI-compatible Chat Completions over HTTPS, or
  loopback HTTP.
- The first implementation buffers each Pi completion before returning
  Responses SSE.
- Generic provider-hosted web search and audio translation are not supported
  for Pi models.
- Model-specific behavior still depends on the endpoint's actual tool,
  reasoning, image, context-window, and error compatibility.
- Composer input is text-only in this adapter.
- Composer uses Cursor-native tools and MCP settings rather than Codex's
  Responses tool-call bridge.
- Cursor SDK `1.0.23` is intentionally pinned to the live-tested version.

## Rollback

Stop the private gateway first:

```bash
sudhir-codex gateway stop
```

Then remove only these exact private targets for your installation:

```text
$HOME/.local/bin/sudhir-codex        # Unix launcher copy
$HOME/.sudhir-codex                  # private state
<checkout>                           # fork checkout and private runtime
```

No official `codex` command or `$HOME/.codex` state needs to be restored.
