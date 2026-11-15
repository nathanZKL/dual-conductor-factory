# Changelog

## 0.2.1

- Orca worker dispatch tested end to end on Orca 1.4.259. Docs and the generated `AGENTS.md` (template v3) now say `orca orchestration worker-start` must run from inside an Orca terminal; elsewhere it fails with `no_active_sender_terminal`. Re-run `ai-eng init` to update existing repositories.
- README: describe what `ai-eng run` commits, pushes and opens.

## 0.2.0

- New `ai-eng run`: automates the whole loop with headless agents (`claude -p`, `codex exec`). The coordinator writes the plan and stops for the owner's edit; `ai-eng run --continue <plan-id>` then runs the plan review loop, builds each task in its owner's worktree (Claude Code with its subagents; Codex with implement/test/research/security workers on their configured models), runs the test command itself, commits, gets the other conductor's review at that exact SHA, loops on NO-GO, and pushes and opens a draft PR on GO. State is saved after every step; `--status` and `--continue` resume. Stops on escalation (3 NO-GO), repeated test failures, or the Claude budget (`automation.budget_usd`, default $20).
- Scoped permissions: Claude builders use `acceptEdits` plus an allowlist (test command, read-only git); the coordinator may write only its plan file; reviewers are read-only. Codex builds in `workspace-write`, reviews in `read-only`. Agents never commit, push or open PRs.

## 0.1.1

- Plan reviews work from a conductor's worktree: `ai-eng review` reads plans from the main checkout, where the coordinator writes them.
- Generated instructions (template v2) tell conductors where plans live and how to run the other conductor's tests (`git switch --detach <branch>`). Re-run `ai-eng init` to update existing repositories.
- README: Homebrew 7 requires `brew trust` for third-party taps; the install steps now say so.
- `scripts/render_formula.py`: use `formula_opt_bin` (passes `brew style`).

## 0.1.0

First release.

- `ai-eng init`: detects the repository, asks for a description, stack and base branch, previews every change with diffs, and writes only after approval. `--dry-run` writes nothing. Existing files are never overwritten: Markdown instructions get a marked ai-eng block, `.claude/settings.json` gets deny rules merged in, and anything else that differs is reported as a conflict. Every edited file is backed up first, and re-running is a no-op.
- Generated workflow: coordinator (Claude Code), Conductor A (Claude Code) and Conductor B (Codex) with configurable model tiers; four Claude Code subagents (implementer, tester, researcher, security-reviewer); Codex worker roles; plan and review templates; PR template; starter CI for Python, Node and mixed projects.
- `ai-eng worktrees`: creates the two conductor worktrees from the base branch; refuses existing folders, checked-out branches and uncommitted policy files.
- `ai-eng review record | check | list`: GO/NO-GO records tied to an exact commit SHA or plan version, shared across worktrees. Refuses self-review, requires findings for NO-GO, and reports ESCALATE after the configured number of NO-GO rounds.
- `ai-eng doctor`: read-only diagnostics with verified, configured, untested, missing and blocked statuses, plus `--json` and `--online` (GitHub CI and branch protection).
- `ai-eng handoff`: sanitized Markdown status with exact SHAs, review state, notes and next actions.
- `ai-eng upgrade --plan`: shows template updates and conflicts without applying them.
- Distribution: zipapp (`ai-eng-<version>.pyz`), wheel and sdist with SHA-256 checksums and build provenance attestation; Homebrew tap for macOS; `uv tool install` / `pipx` for Windows and Linux.
