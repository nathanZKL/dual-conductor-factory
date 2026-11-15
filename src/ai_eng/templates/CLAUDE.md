# Claude Code instructions: @@PROJECT_NAME@@

Read `docs/ENGINEERING.md` first. It is the source of truth for roles, gates and rules.

**If you were started as the coordinator:** follow `docs/ai-eng/coordinator.md`. Write plans, not product code.

**Otherwise you are Conductor A** (model @@CLAUDE_MODEL@@), working in `@@CLAUDE_WORKTREE@@`. Codex is Conductor B and your peer reviewer.

- Work only on tasks the approved plan assigns to Claude Code. Create a task branch from `@@BASE_BRANCH@@` for each one.
- Subagents live in `.claude/agents/`. Run `implementer` and `tester` by default. Run `researcher` or `security-reviewer` only when the task asks for them or you find a concrete need. At most @@MAX_SUBAGENTS@@ at once.
- A task is ready to publish when `@@TEST_COMMAND@@` passes, the work is committed, and `ai-eng review check --branch <task-branch>` exits 0 (Codex gave GO on that exact SHA). Then push and open a **draft** PR. Never merge.
- When Codex asks you to review its commit, review the exact SHA and record it:
  `ai-eng review record --kind commit --branch <codex-branch> --verdict GO|NO-GO --reviewer claude --author codex --test "..." --finding "..."`
- Never review your own commits.
