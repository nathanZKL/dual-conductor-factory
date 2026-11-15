# Proposal: ai-eng dashboard UI (shelved)

Status: **shelved 2026-11-15**. The owner chose to keep using Orca as the UI. Revisit only if Orca stops fitting.

## Decisions already made

| Question | Answer |
|---|---|
| Scope | View + control: start a run, edit the plan, continue, retry. No merge button: only the owner merges, on GitHub. |
| Platform | Desktop app built with Tauri (Mac and Windows). |
| Shipping | Separate download (brew cask, Windows installer). Calls the installed `ai-eng` CLI, which stays the single source of truth. |
| Repos | One repo at a time. |
| Priorities | Run and task status, review verdicts, agent logs. |
| Alerts | OS notifications when the run needs the owner. |
| Plan editing | Built-in editor with JSON validation. |

## Screens

1. **Run view**: tasks with status and owner conductor; pipeline plan, review, build, test, commit, cross-review, PR.
2. **Review panel**: GO/NO-GO per round, findings, the SHA each verdict covers.
3. **Log viewer**: per-step agent logs from `.git/ai-eng/runs/<plan-id>/` (already redacted), including Claude subagent use.
4. **Plan editor**: Markdown plus live validation of the task JSON (IDs, owners, at most one dependency). Continue is disabled while invalid.
5. **Actions**: New run, Continue, Retry.

Notifications: plan ready to edit, task escalated, tests keep failing, budget used up, draft PRs opened.

## CLI work needed first

- `ai-eng run --status --json` and JSON output for `ai-eng review list`, so the app never parses text.
- A per-run lock so the app and a terminal cannot drive the same run at once.
- `ai-eng run --check-plan <plan-id>` so the editor validates exactly as the CLI does.

## Effort

Medium, upper end. The UI reads existing state files and calls existing commands. Most extra cost is the desktop shell, the built-in editor, and installers and signing.

## Open questions

- Code signing: Apple Developer is $99/year. Without it, macOS Gatekeeper and Windows SmartScreen warn on first launch. Ship unsigned at first, or sign from the start?
- Location: `app/` in this repo, or a separate repo?
