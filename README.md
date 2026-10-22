# Codex with Open Models

Codex with Open Models is an independent, point-in-time fork of the
Apache-2.0-licensed
[OpenAI Codex CLI](https://github.com/openai/codex). It lets one Codex CLI use
OpenAI GPT models alongside compatible open-weight and third-party models.

This is a one-time fork, not a continuously synchronized mirror. Selected
upstream Codex changes may be reviewed, tested, and brought across at intervals.
Nothing is merged or released automatically.

This project is not affiliated with, endorsed by, or supported by OpenAI.
OpenAI and Codex are trademarks of their respective owner.

## 0.163.0 candidate upgrade

This branch targets upstream Codex **0.163.0** at
`83086188ae0614bd882818ff1aa23f3194962012`. Native builds are dispatched in
GitHub Actions one platform at a time, starting with Apple Silicon macOS.
The candidate is not an accepted replacement until its recorded artifact checks pass.

GPT inference can use an authenticated Responses WebSocket through the gateway.
Pi and Cursor keep their existing HTTP adapters. Set `SUDHIR_CODEX_WEBSOCKETS=0`
when launching to restore HTTP-only operation. Production-mode external DevTools
uses an ephemeral loopback debugging port; `SUDHIR_CODEX_DEVTOOLS=0` omits it.
The built-in Developer menu remains unavailable in production mode. The official
application, signatures, provider identity and cloud executor are preserved.

## Current stable release

The current stable and GitHub Latest release is
[`openmodels-v0.1.0-rc.12`](https://github.com/upaayan/codex-with-openmodels/releases/tag/openmodels-v0.1.0-rc.12). It was built from
`ea745c5af4afa070c0462cb7465f70429b77b573` and accepted on Apple Silicon macOS and on the supported
Windows/WSL2 deployment using the Linux x64 MUSL backend.

The tag retains its historical `rc.12` suffix for immutable build provenance,
but the GitHub Release is published as a stable, non-prerelease release. Download
the matching platform archive together with `SHA256SUMS` and verify it before
installation.

## Models and access

- **GPT models** use the normal Codex sign-in and the user's existing
  ChatGPT/Codex subscription.
- **Open-weight and other non-GPT models** use the user's own provider API
  keys. Model definitions and keys remain on the user's machine and are read
  from the local Pi model registry.
- **Cursor models** are optional and use the user's own Cursor
  credential through the pinned Cursor SDK worker.

The merged model picker allows a task to switch between these routes without
replacing the official Codex installation.

Open-weight model families with explicit route handling include:

- DeepSeek
- Moonshot Kimi
- Z.AI GLM
- MiniMax
- Qwen
- Google Gemma and DiffusionGemma
- OpenAI gpt-oss
- Xiaomi MiMo
- HY

Exact models depend on the user's local registry and the APIs available from
their chosen providers. Compatible additional models can use the generic
OpenAI Chat Completions, OpenAI Responses, or Anthropic Messages adapters.
Native Gemini Developer API and Google Vertex routes use Pi's pinned
`@google/genai` transport instead of those compatibility adapters. The Pi API
label `google-generative-ai` is an internal discriminator; this project does
not use the deprecated Python `google-generativeai` package.
Model licences and provider terms vary.

## What is included

- The forked Rust CLI and app server.
- A Python gateway that combines GPT and configured non-GPT routes.
- A guarded `sudhir-codex` launcher with state separate from official Codex.
- A pinned Node worker for eight optional Cursor routes.
- GitHub Actions definitions for native Apple Silicon macOS and Linux x64 MUSL
  backend release bundles.

No ChatGPT or Codex desktop-app package, artwork, extracted asset, credential,
API key, authentication file, or local configuration is included.

## Supported release targets

The native release workflow builds:

- Apple Silicon macOS: `aarch64-apple-darwin`
- Ubuntu 22.04 x64: `x86_64-unknown-linux-musl`

The release provides two native runtime archives and one `SHA256SUMS` file.
Windows runs the Linux x64 MUSL backend inside WSL2; WSL1 and a native Windows
backend are not supported release targets. The native Windows Tauri frontend
continues to launch the WSL2 backend through `wsl.exe`.

## Runtime isolation

The launcher uses a separate state directory and refuses overlapping or
symlinked official Codex state. The gateway listens only on
`127.0.0.1:32179`, requires a private client token on every request, and keeps
provider credentials out of model-invoked shell environments.

The repository contains no provider credentials. Model definitions, API keys,
and authentication data remain private inputs on each user-controlled machine.

## Setup and use

See [sudhir_codex/README.md](sudhir_codex/README.md) for the Unix source and
prebuilt installer modes, WSL2 deployment, model IDs, gateway commands, and
rollback notes.

For copy-friendly Windows/WSL2 commands, see
[WINDOWS-QUICKSTART.md](WINDOWS-QUICKSTART.md).

The launch command is deliberately named `sudhir-codex`. Installation never
creates or replaces a command named `codex`.

## Upstream and modifications

The recorded upstream base is in
[.github/upstream-base.txt](.github/upstream-base.txt). A monthly workflow
reports when OpenAI's `main` branch advances; it does not merge or release
anything automatically.

See [MODIFICATIONS.md](MODIFICATIONS.md) for the fork's modification summary.

## Licence

The upstream work and this fork are distributed under the
[Apache License 2.0](LICENSE). Existing attribution is retained in
[NOTICE](NOTICE).
