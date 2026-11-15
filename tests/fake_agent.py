"""Stand-in for the `claude`, `codex` and `gh` CLIs in tests. Behaviour comes from FAKE_SCENARIO (JSON file);
every call is appended to FAKE_CALLS so tests can assert who did what, where."""
import json
import os
import re
import sys
from pathlib import Path


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return default


def main():
    which, args = sys.argv[1], sys.argv[2:]
    scenario = load(os.environ["FAKE_SCENARIO"], {})
    if which == "gh":
        marker = Path(os.environ["FAKE_CALLS"]).with_name("gh-failed-once")
        if scenario.get("gh_fail_once") and not marker.exists():
            marker.write_text("x")
            print("gh: authentication required", file=sys.stderr)
            return 1
        if args[:2] == ["pr", "create"]:
            print(f"https://github.com/example/demo/pull/{args[args.index('--head') + 1].replace('/', '-')}")
        return 0
    prompt = sys.stdin.read()
    role = re.search(r"AI-ENG-ROLE: (\S+)", prompt).group(1)
    task = (re.search(r"AI-ENG-TASK: (\S+)", prompt) or [None, ""])[1]
    cwd = Path(args[args.index("-C") + 1]) if which == "codex" else Path.cwd()
    calls = load(os.environ["FAKE_CALLS"], [])
    calls.append({"agent": which, "role": role, "task": task, "cwd": cwd.name, "args": args})
    Path(os.environ["FAKE_CALLS"]).write_text(json.dumps(calls))
    n = sum(1 for c in calls if c["role"] == role and c["task"] == task)

    def pop(key, default="GO"):
        seq = scenario.get(key, [])
        return seq[n - 1] if n - 1 < len(seq) else default

    if scenario.get("hang") == role:
        import time
        time.sleep(60)
    if n <= scenario.get("fail", {}).get(f"{role}:{task}", 0):
        print("simulated crash", file=sys.stderr)
        return 3

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
        breaks = scenario.get("break_build", {}).get(task, 0)
        if n <= breaks:
            broken.write_text("x")
        elif broken.exists():
            broken.unlink()
        (cwd / f"{task.lower()}.txt").write_text(f"{task} by {which}, pass {n}\n")
        if scenario.get("touch_protected") == task:
            (cwd / "CLAUDE.md").write_text("rewritten by an agent\n")
        if scenario.get("commit_in_build") == task:
            import subprocess
            subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "agent commit"], cwd=cwd, check=True)
    elif role in ("review-plan", "review-commit", "security"):
        key = "plan_verdicts" if role == "review-plan" else f"verdicts_{task}" if role == "review-commit" else "security_verdicts"
        verdict = pop(key)
        findings = [] if verdict == "GO" else [f"{task or 'plan'}: problem {n} -> fix it"]
        text = "Reviewed.\n" + json.dumps({"verdict": verdict, "findings": findings, "tests": ["check: ok"]})

    if which == "claude":
        print(json.dumps({"type": "system", "subtype": "init"}))
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                          "total_cost_usd": float(os.environ.get("FAKE_COST", "0.5"))}))
        print(json.dumps({"type": "system", "subtype": "task_summary"}))  # real claude emits events after the result
    else:
        Path(args[args.index("-o") + 1]).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
