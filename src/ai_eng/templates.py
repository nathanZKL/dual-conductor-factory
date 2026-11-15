"""Template registry and rendering. Templates ship inside the package so they work from a zipapp."""
from __future__ import annotations

import pkgutil
import re
from typing import Dict, List, Tuple

from . import TEMPLATE_VERSION
from .config import ROLES

# How a target file is owned:
BLOCK = "block"            # Markdown: ai-eng owns one marked block; user text around it is kept.
WHOLE = "whole"            # ai-eng owns the whole file (new paths only, or files it wrote before).
JSON_MERGE = "json-merge"  # Add ai-eng's deny rules to existing JSON; never remove anything.

BLOCK_BEGIN = "<!-- ai-eng:begin (managed by ai-eng; edits inside this block are replaced on upgrade) -->"
BLOCK_END = "<!-- ai-eng:end -->"

_PLACEHOLDER = re.compile(r"@@([A-Z0-9_]+)@@")

ROLE_RUNS = {True: "by default", False: "on demand"}
ROLE_AGENT_FILES = {
    "implement": "implementer",
    "test": "tester",
    "research": "researcher",
    "security": "security-reviewer",
}


def load(name: str) -> str:
    data = pkgutil.get_data("ai_eng", "templates/" + name)
    if data is None:
        raise FileNotFoundError(f"template not found: {name}")
    return data.decode("utf-8")


def render(text: str, values: Dict[str, object]) -> str:
    def substitute(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"unknown template placeholder: {key}")
        return str(values[key])

    return _PLACEHOLDER.sub(substitute, text)


def values(cfg: Dict) -> Dict[str, object]:
    project, conductors, roles, limits = cfg["project"], cfg["conductors"], cfg["subagents"], cfg["limits"]
    claude, codex = conductors["claude"], conductors["codex"]
    out: Dict[str, object] = {
        "PROJECT_NAME": project["name"],
        "PROJECT_DESCRIPTION": project.get("description") or "",
        "STACK": project["stack"],
        "BASE_BRANCH": project["base_branch"],
        "TEST_COMMAND": project.get("test_command") or "(no test command set: add project.test_command to .ai-eng/config.json)",
        "COORDINATOR_MODEL": cfg["coordinator"]["model"],
        "CLAUDE_MODEL": claude["model"],
        "CLAUDE_BRANCH": claude["branch"],
        "CLAUDE_WORKTREE": claude["worktree"],
        "CODEX_MODEL": codex["model"],
        "CODEX_EFFORT": codex.get("effort", "default"),
        "CODEX_BRANCH": codex["branch"],
        "CODEX_WORKTREE": codex["worktree"],
        "MAX_SUBAGENTS": limits["max_subagents_per_conductor"],
        "MAX_REVIEW_ROUNDS": limits["max_review_rounds"],
        "TEMPLATE_VERSION": TEMPLATE_VERSION,
    }
    rows = ["| Role | Runs | Claude Code subagent | Codex worker (effort) |", "|---|---|---|---|"]
    for role in ROLES:
        spec = roles[role]
        upper = role.upper()
        out[f"CLAUDE_{upper}_MODEL"] = spec["claude"]["model"]
        out[f"CODEX_{upper}_MODEL"] = spec["codex"]["model"]
        out[f"CODEX_{upper}_EFFORT"] = spec["codex"].get("effort", "default")
        rows.append(
            f"| {role} | {ROLE_RUNS[bool(spec.get('default'))]} | `{ROLE_AGENT_FILES[role]}` ({spec['claude']['model']}) "
            f"| {spec['codex']['model']} ({spec['codex'].get('effort', 'default')}) |"
        )
    out["SUBAGENT_TABLE"] = "\n".join(rows)
    return out


def ci_workflow(cfg: Dict, vals: Dict[str, object]) -> str:
    stack = cfg["project"]["stack"]
    jobs = {"python": ["python"], "node": ["node"], "mixed": ["python", "node"]}[stack]
    parts = [render(load("github/ci-header.yml"), vals)]
    parts += [render(load(f"github/ci-job-{job}.yml"), vals) for job in jobs]
    return "".join(parts)


def target_files(cfg: Dict) -> List[Tuple[str, str, str]]:
    """Every file ai-eng manages for this config: (target path, ownership kind, rendered content)."""
    vals = values(cfg)
    files = [
        ("CLAUDE.md", BLOCK, "CLAUDE.md"),
        ("AGENTS.md", BLOCK, "AGENTS.md"),
        ("docs/ENGINEERING.md", BLOCK, "docs/ENGINEERING.md"),
        ("docs/ai-eng/coordinator.md", WHOLE, "docs/ai-eng/coordinator.md"),
        ("docs/ai-eng/plan-template.md", WHOLE, "docs/ai-eng/plan-template.md"),
        ("docs/ai-eng/review-template.md", WHOLE, "docs/ai-eng/review-template.md"),
        (".github/pull_request_template.md", BLOCK, "github/pull_request_template.md"),
        (".claude/settings.json", JSON_MERGE, "claude/settings.json"),
        (".ai-eng/.gitignore", WHOLE, "ai-eng/gitignore"),
    ]
    files += [(f".claude/agents/{agent}.md", WHOLE, f"claude/agents/{agent}.md") for agent in ROLE_AGENT_FILES.values()]
    out = [(target, kind, render(load(name), vals)) for target, kind, name in files]
    if cfg["project"]["stack"] != "none" and cfg.get("ci", {}).get("enabled", True):
        out.append((".github/workflows/ai-eng-ci.yml", WHOLE, ci_workflow(cfg, vals)))
    return out


def make_block(content: str) -> str:
    return f"{BLOCK_BEGIN}\n{content.strip(chr(10))}\n{BLOCK_END}"


def find_block(text: str):
    """Return (start, end) of the single managed block, None if absent, or 'multiple'."""
    begin = "<!-- ai-eng:begin"
    count = text.count(begin)
    if count == 0:
        return None
    if count > 1 or text.count(BLOCK_END) != 1:
        return "multiple"
    start = text.index(begin)
    end = text.find(BLOCK_END, start)
    if end == -1:
        return "multiple"
    return start, end + len(BLOCK_END)
