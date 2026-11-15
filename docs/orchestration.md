# Orchestration and Orca

ai-eng sets up roles, instructions and worktrees. It does not start agents or move messages between them.

## Without Orca (always works)

1. Start Claude Code in the main checkout and tell it it is the coordinator.
2. Start Claude Code in `../<repo>.worktrees/claude` and Codex in `../<repo>.worktrees/codex`.
3. The coordinator writes the plan; you paste each task section into the owning conductor's session.
4. Conductors ask each other for reviews through you (paste the branch name and SHA).

## With Orca (optional, unverified by ai-eng)

Orca can host the worktrees and start Codex workers with a model per role. `ai-eng worktrees` prints the command to register the repository (`orca repo add --path <repo>`). The generated `AGENTS.md` shows the worker command the Codex conductor can use:

```bash
orca orchestration worker-start --agent codex --model gpt-6.1-terra --effort medium --worktree current --spec-file task.md
```

These commands come from `orca --help` on Orca 1.4.259. ai-eng has not run an end-to-end dispatch, so `ai-eng doctor` reports Orca dispatch as **untested**. Confirm one small worker run by hand before relying on it.

## Subagents

- **Claude Code:** four subagent files in `.claude/agents/` with per-role models (`sonnet`, `sonnet`, `haiku`, `opus` by default).
- **Codex:** no per-role subagent file format was verified, so roles are documented in `AGENTS.md` and run as Orca workers or by the conductor itself.

Change any model in `.ai-eng/config.json` and run `ai-eng init`; ai-eng never picks a new model for you.
