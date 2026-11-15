# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `init` stops with conflicts (exit 2) | A file ai-eng wants to manage exists with different content, or its ai-eng block was edited by hand. | Read the diff. Re-run with `--on-conflict side` to get `<file>.ai-eng-new` next to yours and merge by hand, or `--on-conflict skip` to leave it. |
| `Not running in a terminal` | `init` or `worktrees` was run by a script or agent. | Add `--yes` after reviewing `--dry-run` output. |
| `worktrees` says commit the ai-eng files first | Worktrees start from the base branch, which does not have the new files yet. | Commit them on the base branch, then re-run. |
| `worktrees` refuses a folder or branch | The path exists and is not a worktree, or the branch is checked out elsewhere. | Pick another path or branch in `.ai-eng/config.json`, or clean up by hand. ai-eng never deletes. |
| `review check` says new commits need a new review | The branch moved after the GO. | Ask the other conductor to review the new head. |
| `review record` exits 4 with ESCALATE | Too many NO-GO rounds. | Owner decides: change scope, re-plan, or raise `limits.max_review_rounds`. |
| doctor shows `file:...` as fail | A managed file was edited by hand. | Move your edits outside the ai-eng block (or into config), then re-run `init`. |
| Windows: `ai-eng` not found after `uv tool install` | uv's tool folder is not on PATH. | Run `uv tool update-shell`, then open a new terminal. |
| macOS: `python3` is 3.9 | Stock Command Line Tools Python. | Supported. Homebrew installs use Homebrew's Python instead. |

To undo an `init`, follow the undo lines it printed: delete the files it created and restore edited ones from `.ai-eng/backups/<timestamp>/`.
