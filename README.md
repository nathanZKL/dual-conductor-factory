# dual-conductor-factory (`ai-eng`)

A small CLI that sets up a dual-conductor engineering workflow in any Git repository: a Claude Code coordinator plans, Claude Code and Codex conductors build in separate worktrees, each reviews the other's commits at an exact SHA, and only a human merges.

```
Owner idea -> Coordinator (Claude) plans -> Owner edits plan -> Codex reviews plan (GO / NO-GO loop)
  -> tasks to Conductor A (Claude Code) and Conductor B (Codex), each with up to 4 subagents
  -> tests pass -> commit -> the OTHER conductor reviews that SHA (GO / NO-GO loop, 3 rounds max)
  -> GO: push + draft PR -> owner merges
```

`ai-eng` writes instructions, policies and templates, creates the worktrees, and records review verdicts. It does **not** install or run Claude Code, Codex or Orca, and it never commits, pushes, merges or deletes anything.

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
curl -LO https://github.com/nathanZKL/dual-conductor-factory/releases/download/v0.1.0/ai-eng-0.1.0.pyz
curl -LO https://github.com/nathanZKL/dual-conductor-factory/releases/download/v0.1.0/SHA256SUMS
shasum -a 256 --check --ignore-missing SHA256SUMS
python3 ai-eng-0.1.0.pyz --version
```

**Windows (PowerShell, with [uv](https://docs.astral.sh/uv/))**

```powershell
$v = "0.1.0"
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

Then start Claude Code in the main checkout as the coordinator, Claude Code in the `claude` worktree, and Codex in the `codex` worktree. Each reads its instructions from `CLAUDE.md` / `AGENTS.md` and `docs/ENGINEERING.md`.

| Command | Purpose |
|---|---|
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

`ai-eng doctor` never reports a capability as verified unless it actually checked it. These are always reported as **untested**, because ai-eng cannot prove them offline: model IDs being available on your plan, Claude Code subagents loading, Orca worker dispatch and completion reporting, and direct Claude-to-Codex messaging. Branch protection and CI runs are checked only with `--online`.

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
