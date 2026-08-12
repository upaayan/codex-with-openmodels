# Windows WSL2 Quickstart

Sudhir-Codex uses the Linux x64 MUSL backend inside WSL2. WSL1 and a native
Windows backend are not supported. The native Windows Tauri frontend connects
to this WSL2 backend through `wsl.exe`.

## Prerequisites

- WSL2 with a Linux distribution
- Git
- Python 3.11 or newer
- Node.js 22.13 or newer
- `uv`

Run all commands below in the WSL2 shell, not native PowerShell.

## Install the Linux MUSL release

The current stable GitHub Release is
[`openmodels-v0.1.0-rc.12`](https://github.com/upaayan/codex-with-openmodels/releases/tag/openmodels-v0.1.0-rc.12). Clone that exact tag and use its Linux x64 MUSL
archive. The tag retains its historical `rc.12` suffix, but GitHub marks the
release stable and Latest.

Clone the exact release tag at `$HOME/.playground/sudhir-codex`. Download these
two release files into the checkout:

- `codex-with-openmodels-x86_64-unknown-linux-musl.tar.gz`
- `SHA256SUMS`

Then install without compiling Rust:

```bash
cd "$HOME/.playground/sudhir-codex"
./scripts/install-sudhir-codex \
  --archive ./codex-with-openmodels-x86_64-unknown-linux-musl.tar.gz \
  --checksums ./SHA256SUMS
```

The installer verifies `SHA256SUMS` and the exact archive layout, installs the
CLI and helpers, and installs bundled `codex-resources/bwrap` with executable
permissions.

The launcher is installed at `$HOME/.local/bin/sudhir-codex`; ensure
`$HOME/.local/bin` is on `PATH`, opening a new WSL2 shell if that directory was
just created.

## Verify and sign in

```bash
sudhir-codex --version
sudhir-codex gateway status
sudhir-codex doctor
sudhir-codex login
sudhir-codex models
```

The gateway listens on `127.0.0.1:32179` inside WSL2. WSL2 localhost
forwarding makes it reachable to Windows applications. The Tauri frontend uses
`wsl.exe` to launch `sudhir-codex app-server --stdio` directly.

## Use Sudhir-Codex in WSL2

From the WSL2 project directory:

```bash
sudhir-codex -m gpt-5.6-sol
sudhir-codex -m pi-zai/glm-5.2
sudhir-codex exec -m cursor/composer-latest-fast "Inspect this repository."
```

Use `/model` inside an interactive task to change models. Model definitions and
credentials are read from `$HOME/.pi/agent` inside WSL2.

## Gateway recovery

```bash
sudhir-codex gateway status
sudhir-codex gateway stop
sudhir-codex gateway start
sudhir-codex doctor
```

Installation never creates or replaces a command named `codex`.
