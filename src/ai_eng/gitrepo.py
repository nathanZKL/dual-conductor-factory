"""Read-mostly Git helpers. Every call uses --no-optional-locks so reads never rewrite the index."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .util import run


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str, check: bool = True) -> Optional[str]:
    """Run git in `root`. Returns stdout, or None on failure when check=False."""
    code, out, err = run(["git", "--no-optional-locks", *args], cwd=root)
    if code != 0:
        if check:
            raise GitError(err or out or f"git {' '.join(args)} failed")
        return None
    return out


def repo_root(path: Path) -> Path:
    if not path.is_dir():
        raise GitError(f"not a directory: {path}")
    out = git(path, "rev-parse", "--show-toplevel", check=False)
    if not out:
        raise GitError(f"not inside a Git repository: {path}")
    return Path(out).resolve()


def common_dir(root: Path) -> Path:
    """The .git directory shared by every worktree of this repository."""
    out = git(root, "rev-parse", "--git-common-dir")
    path = Path(out)
    return (path if path.is_absolute() else root / path).resolve()


def origin_url(root: Path) -> Optional[str]:
    return git(root, "remote", "get-url", "origin", check=False)


def default_branch(root: Path) -> str:
    ref = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
    if ref and "/" in ref:
        return ref.split("/", 1)[1]
    return current_branch(root) or "main"


def current_branch(root: Path) -> Optional[str]:
    return git(root, "branch", "--show-current", check=False) or None


def rev(root: Path, ref: str) -> Optional[str]:
    """Full commit SHA for `ref`, or None if it does not resolve."""
    return git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False) or None


def branch_exists(root: Path, name: str) -> bool:
    return rev(root, "refs/heads/" + name) is not None


def dirty_paths(root: Path) -> List[str]:
    out = git(root, "status", "--porcelain", check=False) or ""
    return [line[3:] for line in out.splitlines() if line.strip()]


def file_in_ref(root: Path, ref: str, path: str) -> bool:
    return git(root, "cat-file", "-e", f"{ref}:{path}", check=False) is not None


def worktrees(root: Path) -> List[Dict]:
    out = git(root, "worktree", "list", "--porcelain", check=False) or ""
    items: List[Dict] = []
    current: Dict = {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if current:
                items.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = Path(value).resolve()
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.replace("refs/heads/", "", 1)
        else:
            current[key] = value or True
    return items


def local_branches(root: Path) -> Dict[str, str]:
    out = git(root, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads", check=False) or ""
    pairs = (line.split(" ", 1) for line in out.splitlines() if " " in line)
    return {name: sha for name, sha in pairs}


def ahead_behind(root: Path, base: str, branch: str) -> Optional[tuple]:
    out = git(root, "rev-list", "--left-right", "--count", f"{base}...{branch}", check=False)
    if not out:
        return None
    behind, ahead = out.split()
    return int(ahead), int(behind)
