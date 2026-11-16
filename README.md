# dual-conductor-factory (`ai-eng`)

A small CLI that sets up a dual-conductor engineering workflow in any Git repository: a Claude Code coordinator plans, Claude Code and Codex conductors build in separate worktrees, each reviews the other's commits at an exact SHA, and only a human merges.

```
Owner idea -> Coordinator (Claude) plans -> Owner edits plan -> Codex reviews plan (GO / NO-GO loop)
  -> tasks to Conductor A (Claude Code) and Conductor B (Codex), each with up to 4 subagents
  -> tests pass -> commit -> the OTHER conductor reviews that SHA (GO / NO-GO loop, 3 rounds max)
  -> GO: push + draft PR -> owner merges
```

`ai-eng` writes instructions, policies and templates, creates the worktrees, and records review verdicts. `ai-eng run` also drives Claude Code and Codex as Orca workers, and after its own test run and a GO review it commits, pushes and opens **draft** PRs. It never installs tools, merges, force-pushes or deletes anything.

## Install

Every release has a `.pyz` (single-file app), a wheel and a sdist, plus `SHA256SUMS` and a build provenance attestation. Python 3.9 or newer is required.

**macOS (Homebrew)**

```bash
brew tap nathanzkl/tap
brew trust --formula nathanzkl/tap/ai-eng   # Homebrew 7 asks you to trust third-party formulae once
brew install nathanzkl/tap/ai-eng
```

The formula ([nathanZKL/homebrew-tap](https://github.com/nathanZKL/homebrew-tap)) installs the release `.pyz`, pinned by SHA-256, and runs it with Homebrew's `python@3.13` (installed as a dependency if you do not have it). Read the formula before trusting it.

**macOS or Linux (release file)**

```bash
curl -LO https://github.com/nathanZKL/dual-conductor-factory/releases/download/v0.3.1/ai-eng-0.3.1.pyz
curl -LO https://github.com/nathanZKL/dual-conductor-factory/releases/download/v0.3.1/SHA256SUMS
shasum -a 256 --check --ignore-missing SHA256SUMS
python3 ai-eng-0.3.1.pyz --version
```

**Windows (PowerShell, with [uv](https://docs.astral.sh/uv/))**

```powershell
$v = "0.3.1"
Invoke-WebRequest "https://github.com/nathanZKL/dual-conductor-factory/releases/download/v$v/ai_eng-$v-py3-none-any.whl" -OutFile "ai_eng-$v-py3-none-any.whl"
Invoke-WebRequest "https://github.com/nathanZKL/dual-conductor-factory/releases/download/v$v/SHA256SUMS" -OutFile SHA256SUMS
(Get-FileHash "ai_eng-$v-py3-none-any.whl" -Algorithm SHA256).Hash.ToLower()   # must match the line in SHA256SUMS
uv tool install ".\ai_eng-$v-py3-none-any.whl"
ai-eng --version
```

`pipx install .\ai_eng-<version>-py3-none-any.whl` works the same way. Optional extra check with GitHub CLI: `gh attestation verify <file> --repo nathanZKL/dual-conductor-factory`.

## Use

```bash
cd /path/to/your-repo
ai-eng init --dry-run   # preview: writes nothing
ai-eng init             # asks a few questions, shows diffs, writes after you confirm
git add -A && git commit -m "Add ai-eng workflow"
ai-eng worktrees        # creates ../<repo>.worktrees/claude and ../<repo>.worktrees/codex
ai-eng doctor           # what is configured, verified, untested, missing or blocked
```

### Automated mode (in Orca)

`ai-eng run` starts every agent as an [Orca](https://github.com/stablyai/orca) worker, so you watch and steer the whole run from Orca. Run it from a terminal inside Orca, in the main checkout, after adding the repository to Orca once:

```bash
orca repo add --path "$PWD"                         # once per repository
ai-eng run "Add CSV export to the reports page"   # coordinator writes docs/ai-eng/plans/<id>.md, then stops
# edit the plan until you like it
ai-eng run --continue <id>                          # everything else is automatic
```

`--continue` runs the plan review loop (Codex reviews, the coordinator revises, until GO). Then Claude's tasks and Codex's tasks build **in parallel**, each in its owner's worktree with the configured subagents. ai-eng runs your test command itself, commits, and has the *other* conductor review that exact SHA in a separate review worktree (`../<repo>.worktrees/review-<conductor>`). NO-GO loops back to the author, 3 rounds max. On GO it pushes and opens a draft PR. You merge.

Each agent step gets its own Orca terminal with the exact model and permissions ai-eng chose:
- Claude: `--permission-mode dontAsk` with a tool allowlist.
- Codex builders: `workspace-write` sandbox.
- Codex reviewers: `read-only` sandbox.

Agents never commit, push or open PRs themselves. A run stops and tells you why on escalation, repeated test failures, or an agent timeout. `ai-eng run --status` shows where it is, and `--continue` resumes.

The first time Claude opens a folder, it asks whether you trust it. Codex asks you to review hooks when `~/.codex/hooks.json` changes. ai-eng never answers these: it prints which Orca terminal is waiting, then carries on once you answer there. Spending is not capped in this mode (`automation.budget_usd` is not enforced).

You can also run the conductors interactively: start Claude Code in the main checkout as the coordinator, Claude Code in the `claude` worktree, and Codex in the `codex` worktree. Each reads its instructions from `CLAUDE.md` / `AGENTS.md` and `docs/ENGINEERING.md`.

| Command | Purpose |
|---|---|
| `ai-eng run "<idea>" \| --continue <id> \| --status` | Automated mode. |
| `ai-eng init [--dry-run] [--yes] [--on-conflict stop\|skip\|side]` | Write or update the workflow files. Safe to re-run. |
| `ai-eng worktrees [--dry-run] [--yes]` | Create the two conductor worktrees. |
| `ai-eng review record\|check\|list` | GO/NO-GO verdicts tied to an exact SHA or plan version. |
| `ai-eng doctor [--json] [--online]` | Read-only diagnostics. |
| `ai-eng handoff [-o FILE] [--note TEXT]` | Sanitized status handoff in Markdown. |
| `ai-eng upgrade --plan` | Show template updates without applying them. |

Every command accepts `--repo <path>` to target another repository.

## What gets written

| Path | Owned how |
|---|---|
| `CLAUDE.md`, `AGENTS.md`, `docs/ENGINEERING.md`, `.github/pull_request_template.md` | A marked `ai-eng` block; your text around it is kept. |
| `.claude/settings.json` | Deny rules merged in (force push, hard reset, `gh pr merge`, `.env` reads). Nothing removed. |
| `.claude/agents/*.md`, `docs/ai-eng/*.md`, `.github/workflows/ai-eng-ci.yml` | Whole file; a hand-edited copy is reported as a conflict, never overwritten. |
| `.ai-eng/config.json` | Your settings: models, branches, worktree paths, limits. Edit and re-run `init`. |
| `.ai-eng/manifest.json` | What ai-eng wrote, to detect hand edits. |

Backups of edited files go to `.ai-eng/backups/` (git-ignored). Review records live in `.git/ai-eng/reviews/`, shared by all worktrees and never pushed.

## What is and is not verified

`ai-eng doctor` never reports a capability as verified unless it actually checked it. These are always reported as **untested**, because ai-eng cannot prove them offline: model IDs being available on your plan, Claude Code subagents loading, Orca worker dispatch on your machine, and direct Claude-to-Codex messaging. Branch protection and CI runs are checked only with `--online`.

## Docs

- [Architecture](docs/architecture.md)
- [Security model](docs/security.md)
- [Approvals and reviews](docs/approvals-and-reviews.md)
- [Orchestration and Orca](docs/orchestration.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Windows test checklist](docs/windows-testing.md)
- [Changelog](CHANGELOG.md)

## Develop

```bash
uv run --no-project --with pytest python -m pytest -q
python3 scripts/build_release.py --smoke
```

License: MIT.
