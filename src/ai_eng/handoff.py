"""Sanitized project-status handoff built only from local facts and explicit notes."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List

from . import __version__
from .gitrepo import ahead_behind, current_branch, dirty_paths, local_branches, origin_url, rev, worktrees
from .review import history, load_all, nogo_count, plan_version
from .review import check as review_check
from .util import redact, run
from .worktrees import display


def build(root: Path, cfg: Dict, notes: List[str], online: bool = False) -> str:
    base = cfg["project"]["base_branch"]
    base_sha = rev(root, base)
    branch = current_branch(root)
    head = rev(root, "HEAD")
    dirty = dirty_paths(root)
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    out = [
        f"# Handoff: {cfg['project']['name']}",
        "",
        f"_Generated {stamp} by ai-eng {__version__} from local Git state, ai-eng review records and the notes given. "
        "Nothing here is inferred: a status that is not listed was not recorded._",
        "",
        "## Repository",
        "",
        f"- Origin: {origin_url(root) or 'none'}",
        f"- Base: `{base}` at `{base_sha or 'missing'}`",
        f"- This checkout: `{branch or 'detached HEAD'}` at `{head or 'no commits'}`",
        f"- Uncommitted paths here: {len(dirty)}" + (f" ({', '.join(dirty[:10])}{', ...' if len(dirty) > 10 else ''})" if dirty else ""),
        "",
        "## Conductors and worktrees",
        "",
        "| Conductor | Model | Checked-out branch | SHA | Worktree |",
        "|---|---|---|---|---|",
    ]
    trees = {w["path"]: w for w in worktrees(root) if "path" in w}
    for name, conductor in cfg["conductors"].items():
        tree = trees.get((root / conductor["worktree"]).resolve())
        where = display(tree["path"], root) if tree else "not created"
        branch_now = (tree or {}).get("branch") or ("detached HEAD" if tree else "n/a")
        sha = (tree or {}).get("head") or "none"
        out.append(f"| {conductor.get('label', name)} ({name}) | {conductor['model']} | `{branch_now}` | `{sha}` | {where} |")

    out += ["", "## Branches", "", "| Branch | SHA | Ahead / behind base |", "|---|---|---|"]
    for name, sha in sorted(local_branches(root).items())[:40]:
        counts = ahead_behind(root, base, name) if base_sha else None
        out.append(f"| `{name}` | `{sha}` | {f'{counts[0]} / {counts[1]}' if counts else 'n/a'} |")

    records = load_all(root)
    limit = cfg["limits"]["max_review_rounds"]
    next_actions: List[str] = []
    out += ["", "## Independent reviews", ""]
    keys = sorted({(r["kind"], r["key"]) for r in records})
    if not keys:
        out.append("No review records.")
    for kind, key in keys:
        hist = history(records, kind, key)
        last = hist[-1]
        current = rev(root, "refs/heads/" + key) if kind == "commit" else plan_version(root, key)
        ok, message = review_check(records, kind, key, current)
        out.append(f"- **{kind} `{key}`**: {message}. Reviewer {last['reviewer']}, author {last['author']}, "
                   f"{len(hist)} round(s), {nogo_count(hist)} NO-GO.")
        out += [f"  - Test run: {t}" for t in last.get("tests", [])]
        out += [f"  - Finding: {f}" for f in last.get("findings", [])]
        if last["verdict"] == "NO-GO" and nogo_count(hist) >= limit:
            next_actions.append(f"ESCALATED: {kind} `{key}` hit {limit} NO-GO rounds; the owner must decide.")
        elif last["verdict"] == "NO-GO":
            next_actions.append(f"{last['author']}: address the findings on {kind} `{key}`, then request round {len(hist) + 1}.")
        elif not ok:
            next_actions.append(f"{kind} `{key}` changed after its GO: request a new review.")
        elif kind == "commit":
            next_actions.append(f"{last['author']}: push `{key}` and open a draft PR (GO at the current SHA).")

    reviewed = {key for kind, key in keys if kind == "commit"}
    conductor_branches = {c["branch"] for c in cfg["conductors"].values()}
    for name in sorted(local_branches(root)):
        if name in reviewed or name == base or name in conductor_branches:
            continue
        counts = ahead_behind(root, base, name) if base_sha else None
        if counts and counts[0] > 0:
            next_actions.append(f"`{name}` has {counts[0]} commit(s) ahead of `{base}` with no review record.")

    if online:
        code, text, _ = run(["gh", "pr", "list", "--json", "number,title,headRefName,isDraft,headRefOid,url"], cwd=root, timeout=20)
        out += ["", "## Open pull requests", ""]
        try:
            prs = json.loads(text) if code == 0 else None
        except ValueError:
            prs = None
        if prs is None:
            out.append("Could not read pull requests (gh missing or not authenticated).")
        elif not prs:
            out.append("None.")
        for pr in prs or []:
            out.append(f"- #{pr['number']} {'(draft) ' if pr['isDraft'] else ''}{pr['title']}: `{pr['headRefName']}` at `{pr['headRefOid']}`")

    out += ["", "## Notes and decisions", ""]
    out += [f"- {note}" for note in notes] or ["None given. Add with `--note \"...\"`."]
    out += ["", "## Next actions", ""]
    out += [f"- {a}" for a in next_actions] or ["- None derived from local state."]
    out += ["", "Merges are owner-only. CI results are not recorded here unless `--online` is used."]
    return redact("\n".join(out) + "\n")
