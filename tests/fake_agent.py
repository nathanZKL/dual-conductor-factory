"""Stand-in for the `orca`, `claude`, `codex` and `gh` CLIs in tests.

`orca terminal create` remembers the agent command; `orca orchestration worker-start` then plays that agent
in-process (behaviour from the FAKE_SCENARIO JSON file) and posts its report to a mailbox that
`orca orchestration check` drains. Every agent call is appended to FAKE_CALLS so tests can assert who did what, where.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return default


STATE = Path(os.environ["FAKE_CALLS"]).with_name("fake-orca.json")
LOCK = STATE.with_suffix(".lock")


@contextmanager
def locked():
    """Both conductor lanes call the fakes at once; serialise all shared-file updates."""
    while True:
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL)
            break
        except FileExistsError:
            time.sleep(0.01)
    try:
        yield
    finally:
        os.close(fd)
        os.unlink(str(LOCK))


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def act(which, args, prompt, cwd, scenario):
    """Play one agent. Returns (message type, report body), or None for a worker that never reports."""
    role = re.search(r"AI-ENG-ROLE: (\S+)", prompt).group(1)
    task = (re.search(r"AI-ENG-TASK: (\S+)", prompt) or [None, ""])[1]
    with locked():
        calls = load(os.environ["FAKE_CALLS"], [])
        calls.append({"agent": which, "role": role, "task": task, "cwd": cwd.name, "args": args})
        Path(os.environ["FAKE_CALLS"]).write_text(json.dumps(calls))
    n = sum(1 for c in calls if c["role"] == role and c["task"] == task)

    def pop(key, default="GO"):
        seq = scenario.get(key, [])
        return seq[n - 1] if n - 1 < len(seq) else default

    if scenario.get("hang") == role:
        return None
    if n <= scenario.get("fail", {}).get(f"{role}:{task}", 0):
        return "escalation", "Worker lost without reporting (simulated crash)"

    text = "ok"
    if role in ("plan", "revise-plan"):
        path = cwd / re.search(r"AI-ENG-PLAN-PATH: (\S+)", prompt).group(1)
        path.parent.mkdir(parents=True, exist_ok=True)
        tasks = scenario.get("tasks") or [
            {"id": "T1", "title": "Claude part", "owner": "claude", "files": ["t1.txt"], "acceptance": ["t1 exists"], "subagents": [], "depends_on": []},
            {"id": "T2", "title": "Codex part", "owner": "codex", "files": ["t2.txt"], "acceptance": ["t2 exists"], "subagents": ["security"], "depends_on": []},
        ]
        path.write_text(f"# Plan (version {n}, {role})\n\n```json\n{json.dumps({'tasks': tasks})}\n```\n")
    elif role in ("build", "implement"):
        broken = cwd / "BROKEN"
        if n <= scenario.get("break_build", {}).get(task, 0):
            broken.write_text("x")
        elif broken.exists():
            broken.unlink()
        (cwd / f"{task.lower()}.txt").write_text(f"{task} by {which}, pass {n}\n")
        if scenario.get("touch_protected") == task:
            (cwd / "CLAUDE.md").write_text("rewritten by an agent\n")
        if scenario.get("commit_in_build") == task:
            subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "agent commit"], cwd=cwd, check=True)
    elif role in ("review-plan", "review-commit", "security"):
        key = "plan_verdicts" if role == "review-plan" else f"verdicts_{task}" if role == "review-commit" else "security_verdicts"
        verdict = pop(key)
        findings = [] if verdict == "GO" else [f"{task or 'plan'}: problem {n} -> fix it"]
        if role == "review-commit" and scenario.get("reviewer_edits") == task:
            (cwd / "reviewer-was-here.txt").write_text("x")
        text = json.dumps({"verdict": verdict, "findings": findings, "tests": ["check: ok"]})
    return "worker_done", text


def orca(args, scenario):
    with locked():  # another lane may be rewriting the file: never read it half-written
        state = load(STATE, {"terms": {}, "mail": [], "waits": 0, "next": 0})
    result = {}
    if args[:2] == ["repo", "list"]:
        result = {"repos": [{"path": os.environ["FAKE_REPO"]}]}
    elif args[:2] == ["terminal", "create"]:
        with locked():
            state = load(STATE, state)
            if scenario.get("unknown_once") and scenario["unknown_once"] in opt(args, "--worktree") and not state.get("unknown_seen"):
                state["unknown_seen"] = True  # Orca has not discovered this new worktree yet
                STATE.write_text(json.dumps(state))
                print(json.dumps({"ok": False, "error": {"code": "selector_not_found", "message": "selector_not_found"}}))
                return 1
            state["next"] += 1
            handle = f"term_{state['next']}"
            state["terms"][handle] = {"cwd": opt(args, "--worktree")[len("path:"):], "command": opt(args, "--command")}
            STATE.write_text(json.dumps(state))
        result = {"terminal": {"handle": handle}}
    elif args[:2] == ["terminal", "wait"]:
        with locked():
            state = load(STATE, state)
            state["waits"] += 1
            STATE.write_text(json.dumps(state))
        if state["waits"] <= scenario.get("blocked_waits", 0):
            result = {"wait": {"satisfied": False, "status": "running", "blockedReason": "agent-trust-workspace"}}
        else:
            result = {"wait": {"satisfied": True, "status": "running"}}
    elif args[:2] == ["orchestration", "worker-start"]:
        term = state["terms"][opt(args, "--terminal")]
        argv = shlex.split(term["command"], posix=os.name != "nt")
        argv = [a.strip('"') for a in argv]
        prompt = Path(opt(args, "--spec-file")).read_text()
        did = f"ctx_{opt(args, '--terminal')}"
        outcome = act(argv[0], argv[1:], prompt, Path(term["cwd"]), scenario)
        # Skills named in the task's "Required skills:" line; the worker loads them (tool calls in its transcript)
        # unless the scenario says to skip them once.
        required = re.findall(r"`([^`]+)`", (re.search(r"Required skills:[^\n]*", prompt) or [""])[0])
        if outcome is not None:
            with locked():
                state = load(STATE, state)
                msg = {"id": f"msg_{did}", "type": outcome[0], "body": outcome[1], "payload": {"dispatchId": did}}
                skip = required and scenario.get("skip_skills") and scenario["skip_skills"] in prompt
                skip = skip and not state.get("skipped")  # only the first matching worker skips
                if outcome[0] == "escalation":
                    msg["payload"]["stopReason"] = "lost"  # as real Orca reports a worker that died
                elif argv[0] == "codex":
                    # Real Codex cannot reach the orca CLI from its sandbox: it ends its turn, the reply is in its transcript.
                    msg = {"id": f"esc_{did}", "type": "escalation", "body": "ended its turn without worker_done",
                           "payload": {"dispatchId": did}}
                state.setdefault("transcripts", {})[did] = {"text": outcome[1] if argv[0] == "codex" else None,
                                                            "tools": [] if skip else required}
                if skip:
                    state["skipped"] = True
                    state.setdefault("skill_pending", {})[did] = {"msg": msg, "skills": required}
                if scenario.get("silent") and scenario["silent"] in prompt and not state.get("silenced"):
                    state["silenced"] = True  # ends its turn without reporting; reports when reminded
                    state.setdefault("held", {})[did] = msg
                    msg = {"id": f"esc_{did}", "type": "escalation", "body": "ended its turn without worker_done",
                           "payload": {"dispatchId": did}}
                state["mail"].append(msg)
                STATE.write_text(json.dumps(state))
        result = {"dispatchId": did, "state": "ready"}
    elif args[:2] == ["orchestration", "worker-read"]:
        entry = state.get("transcripts", {}).get(opt(args, "--dispatch")) or {}
        messages = [{"role": "user", "blocks": [{"type": "text", "text": "worker preamble"}]}]
        for skill in entry.get("tools") or []:
            # Claude loads a skill with its Skill tool; Codex reads the skill's SKILL.md with a shell command.
            block = {"type": "tool-call", "name": "Skill", "input": {"skill": skill}} if entry.get("text") is None else \
                {"type": "tool-call", "name": "exec_command", "input": {"cmd": f"cat /x/{skill.split(':')[-1]}/SKILL.md"}}
            messages.append({"role": "assistant", "blocks": [block]})
        if entry.get("text") is not None:
            messages.append({"role": "assistant", "blocks": [{"type": "text", "text": entry["text"]}]})
        result = {"transcript": {"messages": messages, "limited": False}}
    elif args[:2] == ["orchestration", "send"]:
        with locked():
            state = load(STATE, state)
            did = opt(args, "--to")[len("dispatch:"):]
            held = state.get("held", {}).pop(did, None)
            if held:
                state["mail"].append(held)
            pending = state.get("skill_pending", {}).pop(did, None)
            if pending:  # reminded about skills: it loads them and finishes again
                if not scenario.get("skills_never"):
                    state["transcripts"][did]["tools"] = pending["skills"]
                state["mail"].append(dict(pending["msg"], id=pending["msg"]["id"] + "-again"))
            STATE.write_text(json.dumps(state))
    elif args[:2] == ["orchestration", "check"]:
        with locked():
            state = load(STATE, state)
            mail, state["mail"] = state["mail"], []
            STATE.write_text(json.dumps(state))
        if not mail:
            time.sleep(0.2)
        result = {"messages": mail}
    print(json.dumps({"ok": True, "result": result}))
    return 0


def main():
    which, args = sys.argv[1], sys.argv[2:]
    scenario = load(os.environ["FAKE_SCENARIO"], {})
    if which == "orca":
        return orca([a for a in args if a != "--json"], scenario)
    if which == "gh":
        marker = Path(os.environ["FAKE_CALLS"]).with_name("gh-failed-once")
        if scenario.get("gh_fail_once") and not marker.exists():
            marker.write_text("x")
            print("gh: authentication required", file=sys.stderr)
            return 1
        if args[:2] == ["pr", "create"]:
            print(f"https://github.com/example/demo/pull/{args[args.index('--head') + 1].replace('/', '-')}")
        return 0
    return 0  # claude / codex are only ever started through the fake orca


if __name__ == "__main__":
    sys.exit(main())
