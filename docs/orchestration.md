# Orchestration and Orca

ai-eng sets up roles, instructions and worktrees, and `ai-eng run` drives the agents for you, with Orca as the home base.

## Automated: `ai-eng run` in Orca (recommended)

Run `ai-eng run` from a terminal inside Orca, in the main checkout. The repository must be in Orca (`orca repo add --path <repo>`); ai-eng refuses to start otherwise. Nobody relays messages: ai-eng moves results between agents itself.

For each agent step, ai-eng:
1. Opens a new Orca terminal in the right worktree, running the agent with the exact model and permissions below.
2. Waits until the agent is ready. If Claude asks whether you trust the folder, or Codex asks you to review hooks, ai-eng prints which terminal is waiting and carries on after you answer there. It never answers these itself.
3. Hands the agent its task with `orca orchestration worker-start --terminal`.
4. Collects the result:
   - Claude workers report with `worker_done`.
   - Codex workers cannot: Codex's sandbox blocks the `orca` CLI (EPERM). They end their turn, and ai-eng reads their final reply from the worker's Orca transcript (`worker-read --source transcript`).
   - A worker that ends its turn with no reply is reminded once.

| Step | Agent | How it runs | Permissions |
|---|---|---|---|
| Write / revise plan | Coordinator (Claude) | Orca terminal in the main checkout | `dontAsk`; read code, write only `docs/ai-eng/plans/<id>.md` |
| Review plan | Conductor B (Codex) | Orca terminal in the main checkout | `read-only` sandbox |
| Build a Claude task | Conductor A (Claude) + its subagents | Orca terminal in the Claude worktree | `dontAsk`; edit files, test command, read-only git |
| Build a Codex task | Codex workers: research, implement, test, security | Orca terminal in the Codex worktree, one per role | `workspace-write` (research and security: `read-only`) |
| Test + commit | ai-eng | runs `project.test_command`, then `git commit` | n/a |
| Review a commit | The *other* conductor | `../<repo>.worktrees/review-<reviewer>`, detached at the exact SHA | read-only; a verdict from a reviewer that changed files is rejected |
| Push + draft PR | ai-eng | `git push`, `gh pr create --draft` | your Git and gh logins |

Claude's tasks and Codex's tasks run in parallel, one lane per conductor. A task that depends on the other conductor's task waits for it and builds on top of its branch. Reviews use separate review worktrees, so a reviewer never disturbs its own conductor's work. State and per-step logs (with each worker's task and report) live in `.git/ai-eng/runs/`.

Workers start without two global add-ons that stall unattended agents, while your own sessions keep them:
- **brainstorming:** the `superpowers:brainstorming` skill asks clarifying questions before any work. Claude gets `--disallowedTools Skill(superpowers:brainstorming)`; Codex gets `-c skills.config=[{name=...,enabled=false}]`.
- **GateGuard:** the ECC GateGuard hook denies each first edit. Claude gets `--settings {"env":{"ECC_GATEGUARD":"off"}}`. Codex runs no GateGuard hook.

The list is `DISABLED_SKILLS` and `WORKER_ENV` in `src/ai_eng/agents.py`.

Spending is not capped: Claude's `--max-budget-usd` only works in headless print mode, so `automation.budget_usd` is not enforced. Runs are bounded by `agent_timeout_minutes`, `max_fix_attempts` and the NO-GO round limit.

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

`ai-eng doctor` still reports Orca dispatch as **untested**, because it cannot prove dispatch works on your machine without starting a worker. The full `ai-eng run` loop was tested end to end in Orca 1.4.259 (2026-11-15):
- plan review got GO
- both conductors built in parallel
- each reviewed the other's commit, read-only

That run had no `origin` remote, so push and draft PR were not exercised in Orca mode.

## Subagents

- **Claude Code:** four subagent files in `.claude/agents/` with per-role models (`sonnet`, `sonnet`, `haiku`, `opus` by default).
- **Codex:** no per-role subagent file format was verified, so roles are documented in `AGENTS.md` and run as Orca workers or by the conductor itself.

Change any model in `.ai-eng/config.json` and run `ai-eng init`; ai-eng never picks a new model for you.
