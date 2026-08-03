# Modification notice

This repository is an independently maintained derivative of
[`openai/codex`](https://github.com/openai/codex), licensed under Apache-2.0.

Initial public-fork base:
`e19e65317a333ce725b18ac6f1e3bc904b74d2a1`.

The fork changes the following areas:

- private state and guarded launcher behavior;
- a loopback gateway that merges configured open-model routes with Codex
  subscription routes;
- provider request, reasoning, visibility, and tool-call translation;
- an optional pinned Cursor SDK worker;
- model and reasoning selection behavior in the terminal UI and app server;
- native Windows gateway and launcher support;
- public CI, native release packaging, checksums, and upstream-change
  notification.

Git history is the authoritative record of changed files. The fork is not
affiliated with or endorsed by OpenAI and does not include or distribute
official desktop-app packages or assets.
