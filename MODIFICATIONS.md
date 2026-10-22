# Modification notice

This repository is an independently maintained derivative of
[`openai/codex`](https://github.com/openai/codex), licensed under Apache-2.0.

Initial public-fork base:
`e19e65317a333ce725b18ac6f1e3bc904b74d2a1`.

The fork changes the following areas:

- private state and guarded launcher behavior;
- a loopback gateway that merges configured open-model routes with Codex
  subscription routes;
- provider request, route-scoped reasoning, visibility, and tool-call
  translation;
- pinned Cursor SDK and generic Pi OAuth workers;
- model and reasoning selection behavior in the terminal UI and app server;
- Windows deployment through the Linux x64 MUSL backend inside WSL2, with the
  native Tauri frontend launching it through `wsl.exe`;
- public CI, native release packaging, checksums, and upstream-change
  notification.

Git history is the authoritative record of changed files. The fork is not
affiliated with or endorsed by OpenAI and does not include or distribute
official desktop-app packages or assets.

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

