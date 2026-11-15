# Approvals and reviews

## Gates

| Gate | Who | How it is checked |
|---|---|---|
| Plan approved for review | Owner | The owner tells the coordinator to send it. |
| Plan GO | Codex (Conductor B) | `ai-eng review check --plan-id <id>`; a changed plan file invalidates the GO. |
| Commit GO | The conductor that did **not** write the commit | `ai-eng review check --branch <branch>`; any new commit invalidates the GO. |
| Push + draft PR | The author conductor, automatically after commit GO | Policy in `CLAUDE.md` / `AGENTS.md`. |
| Merge | Owner only | `gh pr merge` is denied for Claude Code; GitHub branch protection if enabled. |

## Recording a verdict

```bash
ai-eng review record --branch claude/login-t1 --verdict GO --reviewer codex --author claude \
  --test "python -m pytest: 42 passed" --scope "T1 login form"
ai-eng review record --branch claude/login-t1 --verdict NO-GO --reviewer codex --author claude \
  --finding "src/login.py:40 password compared with ==; use hmac.compare_digest"
```

- The reviewer and author must differ; the command refuses otherwise.
- A NO-GO needs at least one `--finding`.
- After `limits.max_review_rounds` NO-GO verdicts (default 3) on the same plan or branch, the command exits 4 and prints `ESCALATE`: stop and ask the owner.

## When the reviewer is unavailable

The gate is blocked. Record it with `ai-eng handoff --note "commit review blocked: Codex at usage limit"` and wait. Never substitute a self-review or a subagent's review.

## Limits

ai-eng records who the reviewer said they were; it cannot prove which agent ran the command. The protection against self-review is the role setup (separate agents, separate worktrees) plus the recorded author and reviewer.
