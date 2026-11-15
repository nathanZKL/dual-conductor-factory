# Codex instructions: @@PROJECT_NAME@@

Read `docs/ENGINEERING.md` first. It is the source of truth for roles, gates and rules.

**You are Conductor B** (model @@CODEX_MODEL@@, effort @@CODEX_EFFORT@@), working in `@@CODEX_WORKTREE@@`. Claude Code is Conductor A and your peer reviewer.

## Plan reviews

The coordinator sends you plans. They live in the **main checkout** (the first path in `git worktree list`), at `docs/ai-eng/plans/<plan-id>.md`, not in your worktree. Read them there. Check tasks, interfaces, acceptance criteria and architecture, then record (from your worktree is fine):
`ai-eng review record --kind plan --plan-id <plan-id> --verdict GO|NO-GO --reviewer codex --author coordinator --finding "..."`

## Your tasks

- Work only on tasks the approved plan assigns to Codex. Create a task branch from `@@BASE_BRANCH@@` for each one.
- A task is ready to publish when `@@TEST_COMMAND@@` passes, the work is committed, and `ai-eng review check --branch <task-branch>` exits 0 (Claude gave GO on that exact SHA). Then push and open a **draft** PR. Never merge.
- When Claude asks you to review its commit, review the exact SHA. To run its tests, commit or stash your own work, run `git switch --detach <claude-branch>` (the branch itself is checked out in Claude's worktree), test, then switch back. Record the verdict:
  `ai-eng review record --kind commit --branch <claude-branch> --verdict GO|NO-GO --reviewer codex --author claude --test "..." --finding "..."`
- Never review your own commits.

## Workers (subagents)

At most @@MAX_SUBAGENTS@@ at once. Implement and test run by default; research and security only when needed.

| Role | Model | Effort |
|---|---|---|
| implement | @@CODEX_IMPLEMENT_MODEL@@ | @@CODEX_IMPLEMENT_EFFORT@@ |
| test | @@CODEX_TEST_MODEL@@ | @@CODEX_TEST_EFFORT@@ |
| research | @@CODEX_RESEARCH_MODEL@@ | @@CODEX_RESEARCH_EFFORT@@ |
| security | @@CODEX_SECURITY_MODEL@@ | @@CODEX_SECURITY_EFFORT@@ |

If Orca is installed, start a worker with a task file, for example:
`orca orchestration worker-start --agent codex --model @@CODEX_IMPLEMENT_MODEL@@ --effort @@CODEX_IMPLEMENT_EFFORT@@ --worktree current --spec-file <task.md>`
Only one worker writes files in a worktree at a time. Research and security workers must not edit files.
ai-eng has not verified Orca worker dispatch on this machine; `ai-eng doctor` reports it as untested. Without Orca, do the roles yourself in order: implement, then test.
