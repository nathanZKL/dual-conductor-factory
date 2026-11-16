"""Run Claude Code and Codex as Orca workers.

Each agent step gets its own Orca terminal, started with the exact command ai-eng chooses (model, effort, and the
Codex sandbox or the Claude tool allowlist), and then receives its task with `orca orchestration worker-start
--terminal`. Workers report with worker_done; one Mailbox thread reads this terminal's Orca mail and hands each
report to the step waiting for it. ai-eng must itself run inside an Orca terminal, which is where reports arrive.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .util import redact, run, write_text

REPORT = ("\n\nWhen you are done, report with worker_done. The report body must be your final reply as described above "
          "(for a verdict: the JSON object).")
# Codex's sandbox blocks the orca CLI (EPERM), so Codex workers just end their turn; ai-eng reads the final reply
# from the worker's Orca transcript.
REPORT_CODEX = ("\n\nDo not run the orca CLI: your sandbox blocks it. When you are done, end your turn with your final "
                "reply as described above (for a verdict: only the JSON object); ai-eng reads it from your transcript.")
# Startup prompts only the owner may answer. ai-eng waits for them and never answers them itself.
PROMPTS = {
    "agent-trust-workspace": "Claude Code asks whether you trust this folder",
    "agent-hooks-review-prompt": "Codex asks you to review new or changed hooks (~/.codex/hooks.json)",
}


class AgentError(Exception):
    pass


def orca(*args: str, timeout: int = 60) -> Dict:
    """Run one `orca ... --json` command and return its result, or raise AgentError."""
    code, out, err = run(["orca", *args, "--json"], timeout=timeout)
    data = None
    for candidate in (out, out.strip().splitlines()[-1] if out.strip() else ""):
        try:
            data = json.loads(candidate)
            break
        except ValueError:
            continue
    if not isinstance(data, dict):
        raise AgentError(f"orca {' '.join(args[:2])} failed: {(err or out or f'exit {code}')[-300:]}")
    if not data.get("ok"):
        error, result = data.get("error") or {}, data.get("result") or {}
        raise AgentError(f"orca {' '.join(args[:2])}: {error.get('message') or result.get('lastError') or (err or out)[-300:]}")
    return data.get("result") or {}


def _quiet(*args: str) -> None:
    try:
        orca(*args)
    except AgentError:
        pass


def shell_join(argv: List[str]) -> str:
    # shortcut: Windows uses cmd-style quoting, which PowerShell also accepts for these arguments (no $ or `);
    # switch to per-shell quoting if Orca on Windows starts agents with mangled flags.
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def claude_command(model: str, tools: List[str]) -> List[str]:
    # dontAsk: anything outside the allowlist is denied instead of stopping to ask, so workers never stall.
    # Workers report through the orca CLI, so that one command family must be allowed.
    report = ["Bash(orca orchestration send:*)", "Bash(orca orchestration ask:*)"]
    return ["claude", "--model", model, "--permission-mode", "dontAsk", "--allowedTools", *tools, *report]


def codex_command(model: str, effort: str, sandbox: str) -> List[str]:
    return ["codex", "-m", model, "-c", f"model_reasoning_effort={effort}", "-s", sandbox, "-a", "never"]


def preflight(root: Path) -> Optional[str]:
    """Why ai-eng cannot drive agents through Orca from here, or None."""
    if shutil.which("orca") is None:
        return "Orca is not installed or not on PATH: ai-eng run starts every agent as an Orca worker"
    if not os.environ.get("ORCA_TERMINAL_HANDLE"):
        return "run ai-eng run from a terminal inside Orca: workers report back to the Orca terminal that started them"
    try:
        repos = orca("repo", "list").get("repos") or []
    except AgentError as exc:
        return f"Orca is not reachable ({exc}). Start the Orca app, then try again"
    if not any(Path(r.get("path", "")).resolve() == root.resolve() for r in repos):
        return f"this repository is not in Orca yet: orca repo add --path \"{root}\""
    return None


class Mailbox:
    """The only reader of this terminal's Orca mail: `check --wait` marks mail handled, so two readers would steal
    each other's reports."""

    def __init__(self) -> None:
        self.reports: Dict[str, Dict] = {}
        self.cond = threading.Condition()
        self.stopped = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self.stopped:
            try:
                messages = orca("orchestration", "check", "--wait", "--timeout-ms", "20000", timeout=90).get("messages") or []
            except AgentError:
                time.sleep(2)
                continue
            with self.cond:
                for msg in messages:
                    did = (msg.get("payload") or {}).get("dispatchId")
                    if msg.get("type") == "question" and msg.get("id"):
                        # Nobody answers worker questions mid-run; tell the worker to decide or report the blocker.
                        _quiet("orchestration", "reply", "--id", msg["id"], "--body",
                               "No one is available to answer. Decide from the task spec, or report worker_done "
                               "explaining what blocks you.")
                    elif did and msg.get("type") in ("worker_done", "escalation"):
                        self.reports.setdefault(did, msg)
                self.cond.notify_all()

    def wait(self, dispatch_id: str, deadline: float) -> Optional[Dict]:
        with self.cond:
            while dispatch_id not in self.reports:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.cond.wait(min(left, 5))
            return self.reports.pop(dispatch_id)

    def stop(self) -> None:
        self.stopped = True


def _wait_ready(term: str, title: str, deadline: float, say: Callable[[str], None]) -> None:
    told = ""
    while True:
        wait = orca("terminal", "wait", "--terminal", term, "--for", "tui-idle", "--timeout-ms", "15000", timeout=60).get("wait") or {}
        if wait.get("satisfied"):
            return
        if wait.get("status") not in (None, "running"):
            raise AgentError(f"the agent exited before it was ready (exit {wait.get('exitCode')})")
        reason = wait.get("blockedReason") or ""
        if reason and reason != told:
            say(f"[orca] '{title}' is waiting for you: {PROMPTS.get(reason, reason)}. "
                "Answer it in that Orca terminal; ai-eng carries on afterwards.")
            told = reason
        if time.monotonic() > deadline:
            raise AgentError(f"the agent never became ready ({reason or 'no reason given'})")
        if reason:
            time.sleep(3)


def final_reply(dispatch_id: str) -> str:
    """The worker's last assistant text in its Orca transcript, or ''."""
    last, cursor = "", None
    for _ in range(100):  # pages of 50 messages; a step never gets near this
        page = orca("orchestration", "worker-read", "--dispatch", dispatch_id, "--source", "transcript", "--limit", "50",
                    *(["--cursor", cursor] if cursor else []), timeout=60).get("transcript") or {}
        for msg in page.get("messages") or []:
            text = "".join(b.get("text", "") for b in msg.get("blocks") or [] if b.get("type") == "text").strip()
            if msg.get("role") == "assistant" and text:
                last = text
        cursor = page.get("nextCursor")
        if not page.get("limited") or not cursor or not page.get("messages"):
            return last
    return last


def run_agent(mail: Mailbox, cwd: Path, argv: List[str], prompt: str, title: str, timeout_s: int, log: Path,
              say: Callable[[str], None]) -> str:
    """Start `argv` in a new Orca terminal in `cwd`, give it `prompt` as a worker task, return its report body."""
    deadline = time.monotonic() + timeout_s
    command = shell_join(argv)
    for attempt in range(30):  # Orca discovers a just-created worktree (ai-eng's review checkouts) after a short lag
        try:
            term = orca("terminal", "create", "--worktree", f"path:{cwd}", "--title", title, "--command", command)["terminal"]["handle"]
            break
        except AgentError as exc:
            if "selector_not_found" not in str(exc) or attempt == 29:
                raise
            _quiet("worktree", "list")
            time.sleep(2)
    lines, report = [f"$ {command}", f"(cwd {cwd}, Orca terminal {term})"], ""
    try:
        _wait_ready(term, title, deadline, say)
        spec = log.with_suffix(".spec.md")
        write_text(spec, prompt + (REPORT_CODEX if argv[0] == "codex" else REPORT))
        did = orca("orchestration", "worker-start", "--terminal", term, "--worktree", f"path:{cwd}", "--spec-file", str(spec),
                   "--task-title", title, timeout=180)["dispatchId"]
        lines.append(f"dispatch {did}")
        nudged = False
        while True:
            msg = mail.wait(did, deadline)
            if msg is None:
                _quiet("orchestration", "worker-stop", "--dispatch", did)
                raise AgentError(f"timed out after {timeout_s}s (its Orca worker was stopped)")
            report = str(msg.get("body") or "")
            if msg.get("type") == "worker_done":
                return report
            stop = (msg.get("payload") or {}).get("stopReason")
            if stop in ("lost", "exited"):
                raise AgentError(f"the Orca worker stopped without reporting: {report[:300]}")
            # It ended its turn without worker_done but is still there: its final reply is the report.
            reply = final_reply(did)
            if reply:
                lines.append("report read from the worker's transcript")
                _quiet("orchestration", "worker-stop", "--dispatch", did)
                return reply
            if nudged:
                raise AgentError(f"the Orca worker ended its turn twice without a reply: {report[:300]}")
            # It ended its turn without worker_done but is still there: remind it once.
            nudged = True
            lines.append("reminded the worker to send worker_done")
            orca("orchestration", "send", "--to", f"dispatch:{did}", "--subject", "Report now",
                 "--body", "You ended your turn without worker_done. Finish the task if needed, then report with "
                           "worker_done; the report body must be your final reply as the task describes.")
    finally:
        write_text(log, redact("\n".join(lines + ["", "--- task", prompt, "", "--- report", report])) + "\n")
        _quiet("terminal", "close", "--terminal", term)


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
