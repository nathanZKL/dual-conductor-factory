# Windows test checklist

CI runs the full test suite and a zipapp smoke test on `windows-latest`. This checklist covers what CI cannot: a real machine with your shell, PATH and logins. Use PowerShell. Expected results are in brackets.

## 1. Install

1. Install uv if needed: `winget install --id=astral-sh.uv -e` [`uv --version` prints a version].
2. Follow the Windows install steps in the README for the latest release [hash matches the `SHA256SUMS` line; `ai-eng --version` prints the version].
3. If `ai-eng` is not found: `uv tool update-shell`, open a new terminal, retry.

## 2. Initialize a throwaway repository

```powershell
mkdir $env:TEMP\ai-eng-test; cd $env:TEMP\ai-eng-test
git init -b main; Set-Content README.md "test"; Set-Content pyproject.toml ""
git add -A; git commit -m init
ai-eng init --dry-run       # [lists creates; "Dry run: nothing was written."; git status still clean]
ai-eng init                 # [asks 3 questions, shows plan, asks to apply; answer y]
ai-eng init                 # ["Nothing to change"]
git add -A; git commit -m "ai-eng"
ai-eng worktrees            # [creates ..\ai-eng-test.worktrees\claude and \codex; answer y]
git worktree list           # [three entries: main, conductor/claude, conductor/codex]
```

## 3. Checks

```powershell
ai-eng doctor                       # [git verified; claude/codex/gh verified if installed, else missing; worktrees verified]
ai-eng doctor --json | Out-Null     # [exit code 0 if nothing failed: echo $LASTEXITCODE]
git switch -c claude/demo-t1; Set-Content a.txt "a"; git add a.txt; git commit -m a; git switch main
ai-eng review record --branch claude/demo-t1 --verdict GO --reviewer codex --author claude --test "manual: ok"
ai-eng review check --branch claude/demo-t1    # [OK: GO by codex at <sha>]
ai-eng review record --branch claude/demo-t1 --verdict GO --reviewer claude --author claude   # [error: self-review is not allowed]
ai-eng handoff -o -                 # [Markdown with the SHAs; your user folder shown as ~]
```

## 4. Line endings

```powershell
git config core.autocrlf true; git rm -r --cached -q .; git reset -q --hard
ai-eng doctor     # [managed files still "configured", not "fail"]
```

## 5. Report back

Send the output of `ai-eng --version`, `ai-eng doctor`, and any step whose result did not match the brackets. Clean up with `git worktree remove` on both worktrees and delete the folders.
