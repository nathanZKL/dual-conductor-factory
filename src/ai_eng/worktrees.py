"""Plan and create the two conductor worktrees. Refuses anything ambiguous; never deletes."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Tuple

from .config import CONDUCTORS
from .gitrepo import branch_exists, file_in_ref, rev, worktrees

POLICY_FILES = ("CLAUDE.md", "AGENTS.md", "docs/ENGINEERING.md")


def display(path: Path, root: Path) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:  # different drive on Windows
        return str(path)


def plan_worktrees(root: Path, cfg: Dict) -> Tuple[List[Dict], List[str]]:
    base = cfg["project"]["base_branch"]
    problems: List[str] = []
    if rev(root, base) is None:
        problems.append(f"base branch '{base}' does not exist")
    else:
        missing = [p for p in POLICY_FILES if not file_in_ref(root, base, p)]
        if missing:
            problems.append(
                f"commit the ai-eng files to '{base}' first (missing there: {', '.join(missing)}). "
                f"Worktrees start from '{base}', so uncommitted files would not appear in them."
            )
    existing = worktrees(root)
    by_path = {w["path"]: w for w in existing if "path" in w}
    branch_at = {w["branch"]: w["path"] for w in existing if w.get("branch")}
    steps: List[Dict] = []
    seen = set()
    for name in CONDUCTORS:
        conductor = cfg["conductors"][name]
        path = (root / conductor["worktree"]).resolve()
        branch = conductor["branch"]
        shown = display(path, root)
        if path == root or root in path.parents:
            problems.append(f"{name}: worktree path {shown} is inside the main checkout; use a sibling folder")
            continue
        if path in seen:
            problems.append(f"{name}: both conductors use the same worktree path {shown}")
            continue
        seen.add(path)
        current = by_path.get(path)
        if current:
            # Conductors switch their worktree to task branches; the path, not the branch, identifies it.
            steps.append({"name": name, "path": path, "branch": current.get("branch") or "detached HEAD", "status": "exists"})
            continue
        if path.exists():
            problems.append(f"{name}: {shown} already exists and is not a worktree of this repository; refusing to touch it")
            continue
        if branch in branch_at:
            problems.append(f"{name}: branch '{branch}' is already checked out at {display(branch_at[branch], root)}")
            continue
        if branch_exists(root, branch):
            cmd = ["git", "worktree", "add", str(path), branch]
            note = "reuses the existing branch"
        else:
            cmd = ["git", "worktree", "add", "-b", branch, str(path), base]
            note = f"new branch from '{base}'"
        steps.append({"name": name, "path": path, "branch": branch, "status": "create", "cmd": cmd, "note": note})
    return steps, problems
