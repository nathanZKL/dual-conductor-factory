# Changelog

## Unreleased

- Claude Orca workers may use the Skill tool, so they can load your installed skills. Before this, `--permission-mode dontAsk` denied every skill call. Claude's subagents (`.claude/agents/*.md`) still list fixed tools without Skill.

## 0.3.0

- `ai-eng run` now starts every agent as an Orca worker, with Orca as the home base. Run it from a terminal inside Orca. It refuses to start elsewhere, or when the repository is not in Orca. Each step gets its own Orca terminal with the exact model and permissions ai-eng chose:
  - Claude: `dontAsk` plus a tool allowlist.
  - Codex: a `workspace-write` sandbox for builders and `read-only` for reviewers.
- Results come back without the owner relaying anything. Claude reports with `worker_done`. Codex, whose sandbox blocks the `orca` CLI, ends its turn, and ai-eng reads its final reply from the Orca transcript. Silent workers are reminded once.
- Claude's tasks and Codex's tasks build in parallel, one lane per conductor. Cross-conductor dependencies wait and build on the other task's branch.
- Reviews run in separate review worktrees (`../<repo>.worktrees/review-<conductor>`). A verdict from a reviewer that changed files is rejected.
- Startup prompts (Claude folder trust, Codex hook review) are never answered by ai-eng. It says which Orca terminal is waiting and carries on after the owner answers.
- Spending is no longer capped: `automation.budget_usd` is not enforced, because Claude's budget flag only works in headless print mode.
- Generated docs (template v4) describe the Orca mode. Re-run `ai-eng init` to update existing repositories.
- Tested end to end in Orca 1.4.259 against a local repository: plan review, parallel builds, cross reviews. Push and draft PRs were not exercised in Orca mode.

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
