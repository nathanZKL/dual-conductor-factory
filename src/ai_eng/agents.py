"""Run Claude Code and Codex headless. Prompts go on stdin (no command-line length limits)."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .util import redact, run_tree, write_text


@dataclass
class AgentResult:
    ok: bool
    text: str
    cost_usd: float = 0.0
    error: str = ""


def _exec(cmd: List[str], cwd: Path, prompt: str, timeout_s: int, log: Path):
    exe = shutil.which(cmd[0])
    if exe is None:
        return None, f"{cmd[0]} is not installed or not on PATH"
    try:
        code, out, err = run_tree([exe, *cmd[1:]], cwd, timeout_s, input_text=prompt)
    except OSError as exc:
        return None, str(exc)
    write_text(log, redact(f"$ {' '.join(cmd)}\n(cwd {cwd}, exit {'TIMEOUT' if code is None else code})\n\n"
                           f"--- prompt\n{prompt}\n\n--- stdout\n{out}\n\n--- stderr\n{err}\n"))
    if code is None:
        return None, f"{cmd[0]} timed out after {timeout_s}s (its process tree was stopped)"
    return subprocess.CompletedProcess(cmd, code, out, err), ""


def run_claude(cwd: Path, prompt: str, model: str, tools: List[str], permission_mode: str,
               budget_usd: Optional[float], timeout_s: int, log: Path) -> AgentResult:
    # stream-json logs every tool call (including subagent launches); the last line is the result object.
    cmd = ["claude", "-p", "--model", model, "--output-format", "stream-json", "--verbose", "--permission-mode", permission_mode]
    if budget_usd is not None:
        cmd += ["--max-budget-usd", f"{max(budget_usd, 0.01):.2f}"]
    cmd += ["--allowedTools", *tools]  # variadic: keep last
    proc, error = _exec(cmd, cwd, prompt, timeout_s, log)
    if proc is None:
        return AgentResult(False, "", error=error)
    data = None
    for line in reversed(proc.stdout.strip().splitlines()):  # events can follow the result line
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            data = event
            break
    if data is None:
        return AgentResult(False, proc.stdout, error=f"claude exited {proc.returncode} without a result event: {proc.stderr[-300:]}")
    cost = float(data.get("total_cost_usd") or 0.0)
    text = str(data.get("result") or "")
    if data.get("is_error") or data.get("subtype") not in (None, "success"):
        return AgentResult(False, text, cost, f"claude stopped: {data.get('subtype') or 'error'}")
    return AgentResult(True, text, cost)


def run_codex(cwd: Path, prompt: str, model: str, effort: str, sandbox: str, timeout_s: int, log: Path) -> AgentResult:
    last = log.with_suffix(".last.txt")
    cmd = ["codex", "exec", "-m", model, "-c", f"model_reasoning_effort={effort}", "-s", sandbox,
           "-C", str(cwd), "-o", str(last), "-"]
    proc, error = _exec(cmd, cwd, prompt, timeout_s, log)
    if proc is None:
        return AgentResult(False, "", error=error)
    text = last.read_text(encoding="utf-8") if last.exists() else ""
    if proc.returncode != 0:
        return AgentResult(False, text, error=f"codex exited {proc.returncode}: {proc.stderr[-300:]}")
    return AgentResult(True, text)


def extract_json(text: str, key: str) -> Optional[dict]:
    """The last JSON object in `text` that has `key` (fenced or bare)."""
    decoder = json.JSONDecoder()
    found = None
    i = text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict) and key in obj:
            found = obj
        i = text.find("{", end)
    return found
