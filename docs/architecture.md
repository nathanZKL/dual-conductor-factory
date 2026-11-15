# Architecture

`ai-eng` is a standard-library-only Python package (3.9+), shipped as a zipapp, wheel and sdist. It has no runtime dependencies and no daemon, server or network service.

## Modules (`src/ai_eng/`)

| Module | Job |
|---|---|
| `cli.py` | Argument parsing, prompts, output, exit codes. |
| `config.py` | `.ai-eng/config.json` defaults and validation; stack detection. |
| `templates.py` | Template registry, `@@PLACEHOLDER@@` rendering, managed-block markers. Templates ship inside the package (`templates/`) and load with `pkgutil`, so the zipapp works. |
| `planner.py` | Computes create / update / append / merge / unchanged / conflict for every managed file, and applies an approved plan with backups. |
| `worktrees.py` | Plans the two conductor worktrees and refuses ambiguous state. |
| `review.py` | GO/NO-GO records keyed by plan id or branch, tied to a SHA or plan digest. |
| `doctor.py` | Read-only checks with statuses. |
| `handoff.py` | Sanitized Markdown status. |
| `gitrepo.py`, `util.py` | Git calls (`--no-optional-locks`, so reads never rewrite the index), subprocess without a shell, hashing, redaction. |

## Ownership model

- **Block** (`CLAUDE.md`, `AGENTS.md`, `docs/ENGINEERING.md`, PR template): ai-eng owns the text between `<!-- ai-eng:begin ... -->` and `<!-- ai-eng:end -->`. The manifest stores the block's hash; if the block was edited by hand, re-running is a conflict.
- **Whole file** (agents, docs/ai-eng, CI): created only when missing; updated only if the file still matches the hash ai-eng recorded.
- **JSON merge** (`.claude/settings.json`): union of deny rules; nothing is removed or reordered.

Hashes normalise CRLF to LF, so Git's autocrlf on Windows does not look like an edit.

## Exit codes

`0` ok, `1` error or doctor failure, `2` conflicts or blocked (nothing written), `4` review escalation.
