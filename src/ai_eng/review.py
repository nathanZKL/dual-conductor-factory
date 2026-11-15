"""GO/NO-GO review records tied to an exact commit SHA (or plan file version).

Records live in the Git common directory (<repo>/.git/ai-eng/reviews), so every worktree of the
repository sees the same records, and they are never committed or pushed.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .gitrepo import common_dir, worktrees
from .util import digest, read_text

KINDS = ("plan", "commit")
VERDICTS = ("GO", "NO-GO")


class ReviewError(ValueError):
    pass


class EscalatedError(ReviewError):
    pass


def store(root: Path) -> Path:
    return common_dir(root) / "ai-eng" / "reviews"


PLAN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def plan_path(plan_id: str) -> str:
    if not PLAN_ID.match(plan_id) or ".." in plan_id:
        raise ReviewError(f"invalid plan id {plan_id!r}: use letters, digits, '.', '_' and '-' only")
    return f"docs/ai-eng/plans/{plan_id}.md"


def main_checkout(root: Path) -> Path:
    """The main worktree (first in `git worktree list`), where the coordinator writes plans."""
    trees = worktrees(root)
    return trees[0]["path"] if trees and "path" in trees[0] else root


def plan_version(root: Path, plan_id: str) -> Optional[str]:
    """Digest of the plan in the main checkout, so reviewers in other worktrees see the same file."""
    try:
        text = read_text(main_checkout(root) / plan_path(plan_id))
    except (OSError, UnicodeDecodeError):
        return None
    return digest(text) if text is not None else None


def load_all(root: Path) -> List[Dict]:
    folder = store(root)
    records: List[Dict] = []
    if folder.is_dir():
        for path in folder.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if isinstance(data, dict):
                records.append(data)
    records.sort(key=lambda r: (str(r.get("kind")), str(r.get("key")), int(r.get("round", 0))))
    return records


def history(records: List[Dict], kind: str, key: str) -> List[Dict]:
    return [r for r in records if r.get("kind") == kind and r.get("key") == key]


def nogo_count(records: List[Dict]) -> int:
    return sum(1 for r in records if r.get("verdict") == "NO-GO")


def record(
    root: Path,
    kind: str,
    key: str,
    verdict: str,
    reviewer: str,
    author: str,
    sha: Optional[str],
    tests: List[str],
    findings: List[str],
    scope: str = "",
    conditions: str = "",
    max_rounds: Optional[int] = None,
) -> Dict:
    if kind not in KINDS:
        raise ReviewError(f"kind must be one of {KINDS}")
    if verdict not in VERDICTS:
        raise ReviewError(f"verdict must be GO or NO-GO, not {verdict!r}")
    if not reviewer.strip() or not author.strip():
        raise ReviewError("both --reviewer and --author are required")
    if reviewer.strip().lower() == author.strip().lower():
        raise ReviewError("self-review is not allowed: the reviewer must be a different agent than the author")
    if kind == "commit" and not sha:
        raise ReviewError("a commit review needs a commit SHA")
    if kind == "plan" and not sha:
        raise ReviewError(f"plan file {plan_path(key)} not found in the main checkout ({main_checkout(root)}): write the plan before reviewing it")
    if verdict == "NO-GO" and not findings:
        raise ReviewError("a NO-GO needs at least one --finding")
    prior = history(load_all(root), kind, key)
    if max_rounds is not None and nogo_count(prior) >= max_rounds:
        raise EscalatedError(
            f"{kind} '{key}' is escalated after {nogo_count(prior)} NO-GO rounds; only the owner can decide. "
            "The owner may raise limits.max_review_rounds in .ai-eng/config.json or start a new plan or branch."
        )
    rec = {
        "kind": kind,
        "key": key,
        "round": len(prior) + 1,
        "verdict": verdict,
        "reviewer": reviewer.strip(),
        "author": author.strip(),
        "sha": sha,
        "tests": list(tests),
        "findings": list(findings),
        "scope": scope,
        "conditions": conditions,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    folder = store(root)
    folder.mkdir(parents=True, exist_ok=True)
    # Readable name plus a hash of the exact key, so "feat/x" and "feat_x" never collide.
    safe_key = re.sub(r"[^A-Za-z0-9._-]+", "_", key)
    path = folder / f"{kind}-{safe_key}-{digest(key)[:8]}-r{rec['round']:02d}.json"
    # "x" mode: never overwrite an existing record, even if two reviewers race.
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(rec, handle, indent=2)
        handle.write("\n")
    return rec


def check(records: List[Dict], kind: str, key: str, current: Optional[str]) -> Tuple[bool, str]:
    """Is there a GO for exactly the current version? `current` is the head SHA or plan digest."""
    hist = history(records, kind, key)
    if not hist:
        return False, f"no review recorded for {kind} '{key}'"
    last = hist[-1]
    if last.get("verdict") != "GO":
        return False, f"latest verdict for {kind} '{key}' is {last.get('verdict')} (round {last.get('round')})"
    if kind == "commit":
        if current is None:
            return False, f"branch '{key}' not found"
        if last.get("sha") != current:
            return False, (
                f"GO was for {str(last.get('sha'))[:12]}, but '{key}' is now at {current[:12]}: "
                "new commits need a new review"
            )
        return True, f"GO by {last.get('reviewer')} at {current} (round {last.get('round')})"
    if current is None:
        return False, f"plan file for '{key}' not found"
    if last.get("sha") != current:
        return False, f"plan '{key}' changed after its GO: it needs a new review"
    return True, f"GO by {last.get('reviewer')} (round {last.get('round')})"


def summary_markdown(rec: Dict) -> str:
    lines = [
        f"- Reviewer: {rec['reviewer']} (author: {rec['author']})",
        f"- Verdict: {rec['verdict']} (round {rec['round']})",
    ]
    if rec["kind"] == "commit":
        lines.append(f"- Reviewed SHA: `{rec['sha']}`")
    lines += [f"- Test: {t}" for t in rec.get("tests", [])]
    lines += [f"- Finding: {f}" for f in rec.get("findings", [])]
    if rec.get("conditions"):
        lines.append(f"- Conditions: {rec['conditions']}")
    return "\n".join(lines)
