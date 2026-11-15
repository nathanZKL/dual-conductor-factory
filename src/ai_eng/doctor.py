"""Read-only diagnostics. Separates 'files exist' (configured) from 'actually checked' (verified)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .config import ConfigError, load_config, load_manifest
from .gitrepo import current_branch, dirty_paths, origin_url, rev
from .planner import plan
from .review import check as review_check
from .review import history, load_all, nogo_count, plan_version
from .util import redact, run
from .worktrees import plan_worktrees

STATUSES = ("verified", "configured", "untested", "missing", "blocked", "fail")
LEVEL = {"verified": "pass", "configured": "pass", "untested": "warn", "missing": "warn", "blocked": "warn", "fail": "fail"}

TOOLS = (
    ("git", ["git", "--version"], True),
    ("claude", ["claude", "--version"], False),
    ("codex", ["codex", "--version"], False),
    ("orca", ["orca", "--version"], False),
    ("gh", ["gh", "--version"], False),
)

INSTALL_HINTS = {
    "git": "Install Git: https://git-scm.com/downloads",
    "claude": "Install Claude Code: https://docs.anthropic.com/en/docs/claude-code",
    "codex": "Install the Codex CLI: https://github.com/openai/codex",
    "orca": "Optional. Without Orca, dispatch tasks and workers by hand (see docs/ENGINEERING.md).",
    "gh": "Install GitHub CLI and run `gh auth login`: https://cli.github.com",
}


def tool_version(cmd: List[str]) -> Optional[str]:
    code, out, err = run(cmd, timeout=15)
    if code != 0:
        return None
    return (out or err).splitlines()[0].strip() if (out or err) else "installed"


def run_doctor(root: Path, online: bool = False) -> List[Dict]:
    checks: List[Dict] = []

    def add(check_id: str, status: str, detail: str, next_action: str = "", level: Optional[str] = None) -> None:
        checks.append({"id": check_id, "status": status, "level": level or LEVEL[status], "detail": detail, "next": next_action})

    # Repository
    add("repo", "verified", f"Git repository: {root.name}")
    origin = origin_url(root)
    if origin:
        add("repo.origin", "verified", f"origin: {redact(origin)}")
    else:
        add("repo.origin", "missing", "no 'origin' remote", "Add a GitHub remote: git remote add origin <url>")
    dirty = dirty_paths(root)
    add("repo.worktree-state", "verified", f"{len(dirty)} uncommitted path(s)" if dirty else "working tree clean")

    # Tools
    for name, cmd, required in TOOLS:
        version = tool_version(cmd)
        if version:
            add(f"tool.{name}", "verified", version)
        else:
            add(f"tool.{name}", "fail" if required else "missing", f"{name} not found or not runnable", INSTALL_HINTS[name])
    if any(c["id"] == "tool.gh" and c["status"] == "verified" for c in checks):
        code, _, _ = run(["gh", "auth", "status"], cwd=root, timeout=15)
        if code == 0:
            add("tool.gh.auth", "verified", "gh is authenticated (account not shown)")
        else:
            add("tool.gh.auth", "blocked", "gh is not authenticated", "Run: gh auth login")
    if any(c["id"] == "tool.orca" and c["status"] == "verified" for c in checks):
        code, out, _ = run(["orca", "status", "--json"], cwd=root, timeout=15)
        reachable = False
        try:
            reachable = bool(json.loads(out)["result"]["runtime"]["reachable"])
        except (ValueError, KeyError, TypeError):
            pass
        if code == 0 and reachable:
            add("orca.runtime", "verified", "Orca runtime is reachable")
        else:
            add("orca.runtime", "blocked", "Orca is installed but its runtime is not reachable", "Start the Orca app: orca open")
        add("orca.dispatch", "untested", "Orca worker dispatch and completion reporting have not been exercised by ai-eng")

    # Config and managed files
    try:
        cfg = load_config(root)
    except ConfigError as exc:
        add("config", "fail", str(exc), "Fix .ai-eng/config.json, or move it aside and run `ai-eng init`")
        return checks
    if cfg is None:
        add("config", "missing", "no .ai-eng/config.json", "Run: ai-eng init", level="fail")
        return checks
    add("config", "configured", ".ai-eng/config.json is valid")
    base = cfg["project"]["base_branch"]
    if rev(root, base):
        add("repo.base", "verified", f"base branch '{base}' exists")
    else:
        add("repo.base", "fail", f"base branch '{base}' does not exist", "Fix project.base_branch in .ai-eng/config.json")

    for action in plan(root, cfg, load_manifest(root)):
        if action.path.endswith("manifest.json"):
            if action.op != "unchanged":
                add(f"file:{action.path}", "untested", "manifest is out of date", "Run: ai-eng init", level="warn")
            continue
        if action.op == "unchanged":
            add(f"file:{action.path}", "configured", "present and current")
        elif action.op == "create":
            add(f"file:{action.path}", "missing", "not present", "Run: ai-eng init")
        elif action.op in ("update", "append", "merge"):
            add(f"file:{action.path}", "untested", f"out of date ({action.detail})", "Preview: ai-eng upgrade --plan", level="warn")
        else:
            add(f"file:{action.path}", "fail", action.detail, "Resolve by hand, then run: ai-eng init")

    # Worktrees
    steps, problems = plan_worktrees(root, cfg)
    for step in steps:
        if step["status"] == "exists":
            add(f"worktree.{step['name']}", "verified", f"{step['branch']} checked out in its own worktree")
        else:
            add(f"worktree.{step['name']}", "missing", "conductor worktree not created", "Run: ai-eng worktrees")
    for problem in problems:
        add("worktree", "blocked", problem)

    # Capabilities ai-eng cannot prove offline
    models = sorted({cfg["coordinator"]["model"], *(c["model"] for c in cfg["conductors"].values())}
                    | {r[a]["model"] for r in cfg["subagents"].values() for a in ("claude", "codex")})
    add("models", "untested", "model IDs are not validated against providers: " + ", ".join(models),
        "Start each agent once with its model to confirm it is available on your plan")
    add("subagents.claude", "untested", "Claude Code subagent files exist; loading them has not been exercised")
    add("messaging", "untested", "direct Claude <-> Codex messaging is not set up by ai-eng; hand off through the owner or Orca")

    # Reviews
    records = load_all(root)
    branch = current_branch(root)
    if branch and history(records, "commit", branch):
        ok, message = review_check(records, "commit", branch, rev(root, "refs/heads/" + branch))
        add("review.current-branch", "verified" if ok else "untested", message, level="pass" if ok else "warn")
    limit = cfg["limits"]["max_review_rounds"]
    for kind, key in sorted({(r["kind"], r["key"]) for r in records}):
        hist = history(records, kind, key)
        if hist[-1].get("verdict") == "NO-GO" and nogo_count(hist) >= limit:
            add(f"review.{kind}.{key}", "blocked", f"{nogo_count(hist)} NO-GO rounds: escalated to the owner")
        elif kind == "plan":
            ok, message = review_check(records, kind, key, plan_version(root, key))
            add(f"review.plan.{key}", "verified" if ok else "untested", message, level="pass" if ok else "warn")

    if online:
        _online_checks(root, base, add)
    else:
        add("github", "untested", "GitHub CI runs and branch protection not checked", "Run: ai-eng doctor --online")
    return checks


def _online_checks(root: Path, base: str, add) -> None:
    code, out, err = run(["gh", "api", f"repos/{{owner}}/{{repo}}/branches/{base}/protection"], cwd=root, timeout=20)
    if code == 0:
        try:
            contexts = json.loads(out).get("required_status_checks", {}).get("contexts", [])
        except (ValueError, AttributeError):
            contexts = []
        add("github.protection", "verified", f"'{base}' is protected; required checks: {', '.join(contexts) or 'none'}")
    elif "Branch not protected" in out + err:
        add("github.protection", "missing", f"'{base}' has no branch protection: process rules are not enforced by GitHub",
            "Optional: require CI in the repository's branch protection settings")
    else:
        add("github.protection", "blocked", "could not read branch protection (needs gh auth and admin access)")
    code, out, _ = run(["gh", "run", "list", "--workflow", "ai-eng-ci.yml", "--branch", base, "--limit", "1",
                        "--json", "conclusion,headSha"], cwd=root, timeout=20)
    try:
        runs = json.loads(out) if code == 0 else None
    except ValueError:
        runs = None
    if runs:
        run0 = runs[0]
        status = "verified" if run0.get("conclusion") == "success" else "fail"
        add("github.ci", status, f"latest ai-eng CI on '{base}': {run0.get('conclusion') or 'in progress'} at {run0.get('headSha', '')[:12]}")
    elif runs == []:
        add("github.ci", "untested", "ai-eng CI has not run on GitHub yet", "Push the workflow and open a PR")
    else:
        add("github.ci", "blocked", "could not read CI runs (needs gh auth)")


def format_text(checks: List[Dict]) -> str:
    width = max(len(c["id"]) for c in checks)
    lines = [f"{c['status']:<10}  {c['id']:<{width}}  {c['detail']}" for c in checks]
    counts = {level: sum(1 for c in checks if c["level"] == level) for level in ("pass", "warn", "fail")}
    lines.append("")
    lines.append(f"{counts['pass']} pass, {counts['warn']} warn, {counts['fail']} fail")
    actions = [f"- {c['id']}: {c['next']}" for c in checks if c["next"] and c["level"] != "pass"]
    if actions:
        lines += ["", "Next actions:", *actions]
    return "\n".join(lines)
