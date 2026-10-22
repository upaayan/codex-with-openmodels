The changelog can be found on the [releases page](https://github.com/openai/codex/releases).

## Unreleased

- Fix missing command lifecycle events when unified exec cannot create a process, preserving approval rejection and sandbox retry behavior. Thanks @Marvinthebored ([#44557](https://github.com/openai/codex/issues/44557)).

## Codex with Open Models 0.163.0 candidate

- Reconcile the fork and current gateway fixes onto upstream 0.163.0.
- Add GPT WebSockets with per-model HTTP routing for Pi/Cursor and an HTTP-only opt-out.
- Add reversible production-mode external debugging flags to the desktop launcher.
- Build Mac and Linux separately in GitHub Actions, preserving verified archives for reuse.
- Acceptance and installation status are recorded separately from the upstream version target.
