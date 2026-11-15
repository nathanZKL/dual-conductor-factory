# Orchestration and Orca

ai-eng sets up roles, instructions and worktrees, and `ai-eng run` drives the agents for you.

## Automated: `ai-eng run` (recommended)

ai-eng starts each agent headless, one step at a time, and moves results between them itself. Nobody relays messages.

| Step | Agent | How it runs | Permissions |
|---|---|---|---|
| Write / revise plan | Coordinator (Claude) | `claude -p --model <coordinator>` in the main checkout | read code; write only `docs/ai-eng/plans/<id>.md` |
| Review plan | Conductor B (Codex) | `codex exec -m <conductor B model>` | `read-only` sandbox |
| Build a Claude task | Conductor A (Claude) + its subagents | `claude -p` in the Claude worktree | `acceptEdits`, allowlist: test command, read-only git |
| Build a Codex task | Codex workers: research, implement, test, security | `codex exec -m <role model>` in the Codex worktree | `workspace-write` (research and security: `read-only`) |
| Test + commit | ai-eng | runs `project.test_command`, then `git commit` | n/a |
| Review a commit | The *other* conductor | in its own worktree, detached at the exact SHA | read-only |
| Push + draft PR | ai-eng | `git push`, `gh pr create --draft` | your Git and gh logins |

State and per-step logs live in `.git/ai-eng/runs/`. Tasks run one at a time, in dependency order.

## Manual (interactive sessions)

1. Start Claude Code in the main checkout and tell it it is the coordinator.
2. Start Claude Code in `../<repo>.worktrees/claude` and Codex in `../<repo>.worktrees/codex`.
3. The coordinator writes the plan; you paste each task section into the owning conductor's session.
4. Conductors ask each other for reviews through you (paste the branch name and SHA).

## With Orca (optional, for interactive sessions)

Orca can host the worktrees and start Codex workers with a model per role. `ai-eng worktrees` prints the command to register the repository (`orca repo add --path <repo>`). The generated `AGENTS.md` shows the worker command the Codex conductor can use:

```bash
orca orchestration worker-start --agent codex --model gpt-6.1-terra --effort medium --worktree current --spec-file task.md
```

**Run it from inside an Orca terminal.** Orca sends the worker's report back to the terminal that started it. From an ordinary shell the command fails with `no_active_sender_terminal`. `--worktree current` means the worktree of that Orca terminal. From an Orca terminal somewhere else, name the worktree instead: `--worktree path:<path-to-worktree>`.

Tested end to end on Orca 1.4.259 (2026-11-15):
- A Codex worker on `gpt-6.1-terra`, effort `medium`, edited only the files its spec allowed.
- It did not commit.
- It sent back a `worker_done` report with its test result.

`orca orchestration worker-show --dispatch <id>` shows a worker's state from any shell.

`ai-eng doctor` still reports Orca dispatch as **untested**, because it cannot prove dispatch works on your machine without starting a worker. `ai-eng run` does not use Orca: it starts `codex exec` directly for each role.

## Subagents

- **Claude Code:** four subagent files in `.claude/agents/` with per-role models (`sonnet`, `sonnet`, `haiku`, `opus` by default).
- **Codex:** no per-role subagent file format was verified, so roles are documented in `AGENTS.md` and run as Orca workers or by the conductor itself.

Change any model in `.ai-eng/config.json` and run `ai-eng init`; ai-eng never picks a new model for you.
