"""Compute what `init` would change, without writing anything; then apply an approved plan."""
from __future__ import annotations

import copy
import difflib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from . import TEMPLATE_VERSION, __version__
from .config import MANIFEST_PATH, dump
from .templates import BLOCK, JSON_MERGE, WHOLE, find_block, make_block, target_files
from .util import digest, eol_of, read_text, with_eol, write_text

CHANGES = ("create", "update", "append", "merge")


@dataclass
class Action:
    path: str
    op: str  # create | update | append | merge | unchanged | conflict
    detail: str = ""
    old: Optional[str] = None
    new: Optional[str] = None
    record: Optional[str] = None  # hash ai-eng stores in the manifest for this path


def _whole(path: str, cur: Optional[str], new: str, prev: Optional[str]) -> Action:
    if cur is None:
        return Action(path, "create", new=new, record=digest(new))
    if digest(cur) == digest(new):
        return Action(path, "unchanged", record=digest(new))
    if prev and digest(cur) == prev:
        return Action(path, "update", "newer template", old=cur, new=with_eol(new, eol_of(cur)), record=digest(new))
    reason = "edited after ai-eng wrote it" if prev else "file already exists with different content"
    return Action(path, "conflict", reason, old=cur, new=new, record=prev)


def _block(path: str, cur: Optional[str], content: str, prev: Optional[str]) -> Action:
    block = make_block(content)
    if cur is None:
        return Action(path, "create", new=block + "\n", record=digest(block))
    found = find_block(cur)
    if found == "multiple":
        return Action(path, "conflict", "more than one or a broken ai-eng block", old=cur, record=prev)
    eol = eol_of(cur)  # keep the user's line endings (CRLF stays CRLF)
    if found is None:
        new = (cur.rstrip("\r\n") + eol + eol if cur.strip() else "") + with_eol(block, eol) + eol
        return Action(path, "append", "existing content kept; ai-eng block added at the end", old=cur, new=new, record=digest(block))
    start, end = found
    current_block = cur[start:end]
    if digest(current_block) == digest(block):
        return Action(path, "unchanged", record=digest(block))
    new = cur[:start] + with_eol(block, eol) + cur[end:]
    if prev and digest(current_block) == prev:
        return Action(path, "update", "newer template (only the ai-eng block changes)", old=cur, new=new, record=digest(block))
    return Action(path, "conflict", "the ai-eng block was edited by hand", old=cur, new=new, record=prev)


def merge_deny(existing: Dict, template: Dict) -> Dict:
    merged = copy.deepcopy(existing)
    deny = merged.setdefault("permissions", {}).setdefault("deny", [])
    for rule in template["permissions"]["deny"]:
        if rule not in deny:
            deny.append(rule)
    return merged


def _json_merge(path: str, cur: Optional[str], content: str) -> Action:
    template = json.loads(content)
    if cur is None:
        return Action(path, "create", new=dump(template))
    try:
        data = json.loads(cur)
    except ValueError:
        return Action(path, "conflict", "existing file is not valid JSON", old=cur)
    perms = data.get("permissions", {}) if isinstance(data, dict) else None
    if not isinstance(perms, dict) or not isinstance(perms.get("deny", []), list):
        return Action(path, "conflict", "unexpected structure for permissions.deny", old=cur)
    merged = merge_deny(data, template)
    if merged == data:
        return Action(path, "unchanged")
    added = len(merged["permissions"]["deny"]) - len(perms.get("deny", []))
    return Action(path, "merge", f"adds {added} deny rule(s); existing settings kept", old=cur, new=with_eol(dump(merged), eol_of(cur)))


def plan(root: Path, cfg: Dict, manifest: Dict) -> List[Action]:
    recorded = manifest.get("files", {}) if isinstance(manifest.get("files"), dict) else {}
    actions: List[Action] = []
    for path, kind, content in target_files(cfg):
        try:
            cur = read_text(root / path)
        except UnicodeDecodeError:
            actions.append(Action(path, "conflict", "existing file is not UTF-8 text", record=recorded.get(path)))
            continue
        except OSError as exc:  # a directory, or unreadable
            actions.append(Action(path, "conflict", f"cannot read existing path: {exc.strerror or exc}", record=recorded.get(path)))
            continue
        if kind == WHOLE:
            actions.append(_whole(path, cur, content, recorded.get(path)))
        elif kind == BLOCK:
            actions.append(_block(path, cur, content, recorded.get(path)))
        elif kind == JSON_MERGE:
            actions.append(_json_merge(path, cur, content))
    files = {a.path: a.record for a in actions if a.record}
    new_manifest = {"tool_version": __version__, "template_version": TEMPLATE_VERSION, "files": dict(sorted(files.items()))}
    text = dump(new_manifest)
    try:
        cur = read_text(root / MANIFEST_PATH)
    except (OSError, UnicodeDecodeError):
        cur = ""
    if cur is None:
        actions.append(Action(MANIFEST_PATH, "create", "records what ai-eng manages", new=text))
    elif cur.replace("\r\n", "\n") != text:
        actions.append(Action(MANIFEST_PATH, "update", "records what ai-eng manages", old=cur, new=text))
    return actions


def diff(action: Action) -> str:
    lines = difflib.unified_diff(
        (action.old or "").splitlines(keepends=True),
        (action.new or "").splitlines(keepends=True),
        fromfile=f"a/{action.path}",
        tofile=f"b/{action.path}",
    )
    return "".join(lines)


class StaleError(RuntimeError):
    pass


def _unchanged_since_plan(root: Path, action: Action) -> bool:
    try:
        cur = read_text(root / action.path)
    except (OSError, UnicodeDecodeError):
        return False
    if action.op == "create":
        return cur is None
    return cur is not None and action.old is not None and digest(cur) == digest(action.old)


def apply(root: Path, actions: List[Action], on_conflict: str) -> List[str]:
    """Write approved changes. Backs up every existing file first. Returns undo instructions.

    Raises StaleError, writing nothing, if any file changed after the plan was shown.
    """
    stale = [a.path for a in actions if a.op in CHANGES and not _unchanged_since_plan(root, a)]
    if stale:
        raise StaleError("changed after the preview, nothing was written; run again: " + ", ".join(stale))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_dir = root / ".ai-eng" / "backups" / stamp
    suffix = 1
    while backup_dir.exists():
        suffix += 1
        backup_dir = root / ".ai-eng" / "backups" / f"{stamp}-{suffix}"
    undo: List[str] = []
    for action in actions:
        target = root / action.path
        if action.op in ("update", "append", "merge"):
            backup = backup_dir / action.path
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            write_text(target, action.new or "")
            undo.append(f"{action.path}: restore from {backup.relative_to(root).as_posix()}")
        elif action.op == "create":
            write_text(target, action.new or "")
            undo.append(f"{action.path}: delete (new file)")
        elif action.op == "conflict" and on_conflict == "side" and action.new is not None:
            side = action.path + ".ai-eng-new"
            n = 1
            while (root / side).exists():  # never overwrite an earlier proposal the user may be merging
                n += 1
                side = f"{action.path}.ai-eng-new.{n}"
            write_text(root / side, action.new)
            undo.append(f"{side}: delete (proposed version; original left untouched)")
    return undo
