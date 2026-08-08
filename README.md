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

## Models and access

- **GPT models** use the normal Codex sign-in and the user's existing
  ChatGPT/Codex subscription.
- **Open-weight and other non-GPT models** use the user's own provider API
  keys. Model definitions and keys remain on the user's machine and are read
  from the local Pi model registry.
- **Cursor Composer models** are optional and use the user's own Cursor
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
Model licences and provider terms vary.

## What is included

- The forked Rust CLI and app server.
- A Python gateway that combines GPT and configured non-GPT routes.
- A guarded `sudhir-codex` launcher with state separate from official Codex.
- A pinned Node worker for four optional Cursor Composer routes.
- GitHub Actions definitions for native Apple Silicon macOS and Linux x64 MUSL
  backend release bundles.

No ChatGPT or Codex desktop-app package, artwork, extracted asset, credential,
API key, authentication file, or local configuration is included.

## Supported release targets

The initial release workflow builds:

- Apple Silicon macOS: `aarch64-apple-darwin`
- Ubuntu 22.04 x64: `x86_64-unknown-linux-musl`

Release archives contain native runtime binaries and one `SHA256SUMS` file.
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
