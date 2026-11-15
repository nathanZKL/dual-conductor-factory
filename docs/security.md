# Security model

## What ai-eng does and never does

- Writes only the files listed in the README, after showing a diff and getting confirmation (or `--yes`). Backs up every file it edits.
- Never commits, pushes, merges, deletes branches or worktrees, changes Git config, changes repository settings, or installs other tools.
- Never reads, stores or asks for credentials. It relies on your existing `git`, `gh`, Claude Code and Codex logins.
- Runs subprocesses without a shell, with fixed arguments.
- `doctor` and `--dry-run` are read-only; tests prove it by snapshotting the whole repository, including `.git`.
- `doctor --online` and `handoff --online` call GitHub through `gh` for this repository only. Nothing else leaves the machine.

## Generated guardrails

- `.claude/settings.json` deny rules stop Claude Code from force-pushing, hard-resetting, deleting branches or worktrees, merging PRs, and reading `.env` files. They are merged into existing settings and never broaden permissions.
- Codex gets no generated permission file in this version, because the Codex rules format could not be verified for the installed CLI. Its limits are written as policy in `AGENTS.md` only.
- Neither is a sandbox. Branch protection on GitHub is the only hard gate on merging, and `doctor --online` reports whether it is on.

## Handoffs and reviews

- Handoffs redact GitHub, OpenAI-style, AWS and Slack tokens, private key blocks, credentials in URLs, and your home directory path. They are written locally; read them before sharing.
- Review records stay in `.git/ai-eng/reviews/` and are never pushed.

## Release integrity

Releases are built by GitHub Actions from a tag that must match the package version. Each release has `SHA256SUMS` and a build provenance attestation (`gh attestation verify <file> --repo nathanZKL/dual-conductor-factory`). A checksum from the same release page proves the download is intact, not who built it; the attestation and the Homebrew formula's pinned hash narrow that gap.
