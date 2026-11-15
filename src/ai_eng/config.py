"""Project configuration (.ai-eng/config.json) and the tool-owned manifest."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

CONFIG_PATH = ".ai-eng/config.json"
MANIFEST_PATH = ".ai-eng/manifest.json"
STACKS = ("python", "node", "mixed", "none")
ROLES = ("implement", "test", "research", "security")
CONDUCTORS = ("claude", "codex")
MAX_SUBAGENTS_CAP = 4

TEST_COMMANDS = {
    "python": "python -m pytest",
    "node": "npm test",
    "mixed": "python -m pytest && npm test",
    "none": "",
}


class ConfigError(ValueError):
    pass


def detect_stack(root: Path) -> str:
    python = any((root / name).exists() for name in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"))
    node = (root / "package.json").exists()
    if python and node:
        return "mixed"
    return "python" if python else "node" if node else "none"


def default_config(name: str, description: str, stack: str, base_branch: str) -> Dict:
    def role(default: bool, claude: str, codex: str, effort: str) -> Dict:
        return {"default": default, "claude": {"model": claude}, "codex": {"model": codex, "effort": effort}}

    return {
        "schema": 1,
        "project": {
            "name": name,
            "description": description,
            "stack": stack,
            "base_branch": base_branch,
            "test_command": TEST_COMMANDS[stack],
        },
        "coordinator": {"agent": "claude", "model": "opus"},
        "conductors": {
            "claude": {
                "label": "Conductor A",
                "agent": "claude",
                "model": "opus",
                "branch": "conductor/claude",
                "worktree": f"../{name}.worktrees/claude",
            },
            "codex": {
                "label": "Conductor B",
                "agent": "codex",
                "model": "gpt-6.1-sol",
                "effort": "high",
                "branch": "conductor/codex",
                "worktree": f"../{name}.worktrees/codex",
            },
        },
        "subagents": {
            "implement": role(True, "sonnet", "gpt-6.1-terra", "medium"),
            "test": role(True, "sonnet", "gpt-6.1-terra", "medium"),
            "research": role(False, "haiku", "gpt-6-luna", "low"),
            "security": role(False, "opus", "gpt-6.1-sol", "high"),
        },
        "limits": {"max_subagents_per_conductor": MAX_SUBAGENTS_CAP, "max_review_rounds": 3},
        "ci": {"enabled": stack != "none"},
    }


def _need(cfg: Dict, *path: str):
    node = cfg
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"missing setting: {'.'.join(path)}")
        node = node[key]
    return node


def validate(cfg: Dict) -> Dict:
    if _need(cfg, "schema") != 1:
        raise ConfigError("unsupported config schema (expected 1)")
    if _need(cfg, "project", "stack") not in STACKS:
        raise ConfigError(f"project.stack must be one of {', '.join(STACKS)}")
    for key in ("name", "base_branch"):
        if not str(_need(cfg, "project", key)).strip():
            raise ConfigError(f"project.{key} is empty")
    _need(cfg, "coordinator", "model")
    for name in CONDUCTORS:
        for key in ("model", "branch", "worktree"):
            if not str(_need(cfg, "conductors", name, key)).strip():
                raise ConfigError(f"conductors.{name}.{key} is empty")
    if cfg["conductors"]["claude"]["branch"] == cfg["conductors"]["codex"]["branch"]:
        raise ConfigError("the two conductors must use different branches")
    for role in ROLES:
        _need(cfg, "subagents", role, "claude", "model")
        _need(cfg, "subagents", role, "codex", "model")
    limit = _need(cfg, "limits", "max_subagents_per_conductor")
    if not isinstance(limit, int) or not 1 <= limit <= MAX_SUBAGENTS_CAP:
        raise ConfigError(f"limits.max_subagents_per_conductor must be 1-{MAX_SUBAGENTS_CAP}")
    rounds = _need(cfg, "limits", "max_review_rounds")
    if not isinstance(rounds, int) or rounds < 1:
        raise ConfigError("limits.max_review_rounds must be a positive integer")
    return cfg


def dump(obj) -> str:
    return json.dumps(obj, indent=2) + "\n"


def load_config(root: Path) -> Optional[Dict]:
    path = root / CONFIG_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{CONFIG_PATH} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_PATH} must contain a JSON object")
    return validate(data)


def load_manifest(root: Path) -> Dict:
    path = root / MANIFEST_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
