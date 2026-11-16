"""`ai-eng run`: drive the dual-conductor loop with every agent running as an Orca worker.

Humans do two things: edit the plan, and merge PRs. Everything between is automatic:
plan review loop (Codex <-> coordinator), building each task in its owner's worktree, ai-eng's own
test run, commit, review by the *other* conductor at that exact SHA, fix loop, push and draft PR.
Claude's tasks and Codex's tasks run in parallel, one lane per conductor; reviews happen in separate
review worktrees, so a reviewer never disturbs its own conductor's work.

Agents never commit, push or open PRs; ai-eng does, after its own checks. State is saved after every
step in .git/ai-eng/runs/<plan-id>.json, so `ai-eng run --continue <plan-id>` resumes where it stopped.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from . import review
from .agents import AgentError, Mailbox, claude_command, codex_command, extract_json, preflight, run_agent
from .config import ROLES
from .gitrepo import common_dir, current_branch, dirty_paths, git, origin_url, rev
from .util import read_text, run, run_tree, write_text
from .worktrees import plan_worktrees

# Paths an agent must never change in a task commit (workflow policy, permissions, CI, secrets).
PROTECTED = (".ai-eng/", ".claude/", ".codex/", ".github/workflows/", "CLAUDE.md", "AGENTS.md", "docs/ENGINEERING.md", ".env")
# Build leftovers never committed even without a .gitignore.
JUNK = (":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc", ":(exclude,glob)**/.pytest_cache/**",
        ":(exclude,glob)**/node_modules/**")

DEFAULTS = {"agent_timeout_minutes": 30, "test_timeout_minutes": 15, "max_fix_attempts": 2}
OTHER = {"claude": "codex", "codex": "claude"}
READ_TOOLS = ["Read", "Grep", "Glob"]
VERDICT_FORMAT = ('Your final reply (and worker_done report body) must be this JSON object and nothing else:\n'
                  '{"verdict": "GO" or "NO-GO", "findings": ["<file:line or section>: <problem> -> <expected fix>", ...], '
                  '"tests": ["<command>: <result>", ...]}')
DONE = ("published", "ready")


class RunStop(Exception):
    """Stop cleanly with a message. State is saved; the run can be continued."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def settings(cfg: Dict) -> Dict:
    out = dict(DEFAULTS)
    out.update(cfg.get("automation") or {})
    return out


def slugify(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-").split("-")
    return "-".join(words[:6])[:40].strip("-") or "feature"


def validate_tasks(data: Optional[dict]) -> List[str]:
    if not data or not isinstance(data.get("tasks"), list) or not data["tasks"]:
        return ['no ```json block with a non-empty "tasks" list']
    problems, ids = [], set()
    for i, task in enumerate(data["tasks"]):
        if not isinstance(task, dict):
            problems.append(f"task #{i + 1} is not an object")
            continue
        tid = str(task.get("id", ""))
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]*$", tid) or tid in ids:
            problems.append(f"task #{i + 1}: id must be unique letters/digits/dashes")
        ids.add(tid)
        if task.get("owner") not in OTHER:
            problems.append(f"{tid}: owner must be 'claude' or 'codex'")
        if not str(task.get("title", "")).strip():
            problems.append(f"{tid}: title is empty")
        extra = set(task.get("subagents") or []) - set(ROLES)
        if extra:
            problems.append(f"{tid}: unknown subagents {sorted(extra)}")
    for task in data["tasks"]:
        deps = task.get("depends_on") or []
        if len(deps) > 1:
            problems.append(f"{task.get('id')}: at most one dependency per task (it is built on top of that task's branch)")
        for dep in deps:
            if dep not in ids:
                problems.append(f"{task.get('id')}: depends on unknown task {dep}")
    if not problems:
        try:
            ordered(data["tasks"])
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def ordered(tasks: List[dict]) -> List[dict]:
    done, out, pending = set(), [], list(tasks)
    while pending:
        ready = [t for t in pending if set(t.get("depends_on") or []) <= done]
        if not ready:
            raise ValueError("task dependencies form a cycle")
        for t in ready:
            out.append(t)
            done.add(t["id"])
            pending.remove(t)
    return out


class Run:
    def __init__(self, root: Path, cfg: Dict, plan_id: str):
        self.root, self.cfg, self.plan_id = root, cfg, plan_id
        self.opts = settings(cfg)
        self.plan_rel = review.plan_path(plan_id)
        runs = common_dir(root) / "ai-eng" / "runs"
        self.state_path = runs / f"{plan_id}.json"
        self.log_dir = runs / plan_id
        text = read_text(self.state_path)
        self.state: Optional[Dict] = json.loads(text) if text else None
        # One lock guards state, Git and output. Each thread holds it except while waiting on an agent, tests or
        # the network (see unlocked), so the two conductor lanes never touch state at the same time.
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.mail: Optional[Mailbox] = None
        self.errors: List[RunStop] = []
        self.finished: set = set()  # task ids settled in this session
        self.lanes_done: set = set()
        self.logs = len(list(self.log_dir.glob("*.log"))) if self.log_dir.exists() else 0

    # ------------------------------------------------------------ plumbing

    def save(self) -> None:
        with self.lock:
            write_text(self.state_path, json.dumps(self.state, indent=2) + "\n")
            self.cond.notify_all()

    def say(self, message: str) -> None:
        with self.lock:
            print(message, flush=True)
            self.state.setdefault("events", []).append(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}")
            self.save()

    @contextmanager
    def unlocked(self):
        """Let the other lane work while this thread waits on something slow. Never touch state inside."""
        self.lock.release()
        try:
            yield
        finally:
            self.lock.acquire()

    def new_log(self, step: str) -> Path:
        with self.lock:
            self.logs += 1
            return self.log_dir / f"{self.logs:02d}-{step}.log"

    def worktree(self, who: str) -> Path:
        return (self.root / self.cfg["conductors"][who]["worktree"]).resolve()

    def test_command(self) -> str:
        return (self.cfg["project"].get("test_command") or "").strip()

    def bash_allow(self) -> List[str]:
        parts = [p.strip() for p in re.split(r"&&|\|\||;|\|", self.test_command()) if p.strip()]
        return [f"Bash({p}:*)" for p in parts] + [f"Bash({c}:*)" for c in ("git status", "git diff", "git log", "git show")]

    def claude(self, cwd: Path, prompt: str, tools: List[str], step: str, model: Optional[str] = None) -> str:
        return self._agent(claude_command(model or self.cfg["conductors"]["claude"]["model"], tools), cwd, prompt, step)

    def codex(self, cwd: Path, prompt: str, sandbox: str, step: str, role: Optional[str] = None) -> str:
        spec = self.cfg["subagents"][role]["codex"] if role else self.cfg["conductors"]["codex"]
        return self._agent(codex_command(spec["model"], spec.get("effort", "medium"), sandbox), cwd, prompt, step)

    def _agent(self, argv: List[str], cwd: Path, prompt: str, step: str) -> str:
        log = self.new_log(step)
        with self.unlocked():
            try:
                return run_agent(self.mail, cwd, argv, prompt, f"ai-eng {self.plan_id} {step}",
                                 int(self.opts["agent_timeout_minutes"] * 60), log, self.say)
            except AgentError as exc:
                raise RunStop(f"{step}: {exc}. Log: {log}")

    def verdict(self, text: str, step: str) -> Dict:
        data = extract_json(text, "verdict")
        if not data or data.get("verdict") not in ("GO", "NO-GO"):
            raise RunStop(f"{step}: the reviewer did not end with a GO/NO-GO JSON verdict (see the log in {self.log_dir})")
        findings = [str(f) for f in data.get("findings") or []]
        if data["verdict"] == "NO-GO" and not findings:
            findings = ["reviewer gave NO-GO without listing findings"]
        return {"verdict": data["verdict"], "findings": findings, "tests": [str(t) for t in data.get("tests") or []]}

    def plan_text(self) -> str:
        text = read_text(self.root / self.plan_rel)
        if text is None:
            raise RunStop(f"{self.plan_rel} is missing")
        return text

    def tasks(self) -> List[dict]:
        data = extract_json(self.plan_text(), "tasks")
        problems = validate_tasks(data)
        if problems:
            raise RunStop(f"the task list in {self.plan_rel} is invalid: " + "; ".join(problems)
                          + f". Fix the ```json block, then: ai-eng run --continue {self.plan_id}")
        return ordered(data["tasks"])

    # ------------------------------------------------------------ plan

    def write_plan(self, prompt: str, step: str) -> None:
        before = set(dirty_paths(self.root))
        tools = READ_TOOLS + [f"Write({self.plan_rel})", f"Edit({self.plan_rel})"]
        self.claude(self.root, prompt, tools, step, model=self.cfg["coordinator"]["model"])
        stray = set(dirty_paths(self.root)) - before - {self.plan_rel, "docs/ai-eng/plans/", "docs/ai-eng/"}
        stray = {p for p in stray if not p.startswith("docs/ai-eng/plans/")}
        if stray:
            raise RunStop(f"{step}: the coordinator changed files other than the plan: {', '.join(sorted(stray))}. Review them by hand.")
        if read_text(self.root / self.plan_rel) is None:
            raise RunStop(f"{step}: the coordinator did not write {self.plan_rel} (log in {self.log_dir})")

    def plan_review_loop(self) -> None:
        self.state["status"] = "plan-review"
        limit = self.cfg["limits"]["max_review_rounds"]
        while True:
            records = review.load_all(self.root)
            current = review.plan_version(self.root, self.plan_id)
            if review.check(records, "plan", self.plan_id, current)[0]:
                self.say("[plan] Codex gave GO")
                return
            hist = review.history(records, "plan", self.plan_id)
            if hist and hist[-1]["verdict"] == "NO-GO" and hist[-1].get("sha") == current:
                if review.nogo_count(hist) >= limit:
                    self.state["status"] = "escalated"
                    raise RunStop(f"plan '{self.plan_id}' got {limit} NO-GO verdicts: you decide. Findings: "
                                  + "; ".join(hist[-1]["findings"]))
                self.say(f"[plan] coordinator revising the plan ({len(hist[-1]['findings'])} finding(s))")
                self.write_plan(prompt_revise_plan(self, hist[-1]["findings"]), "coordinator-revise")
                if review.plan_version(self.root, self.plan_id) == current:
                    raise RunStop("the coordinator did not change the plan after a NO-GO; edit it yourself, then continue")
                continue
            self.tasks()  # never send an invalid task list to review
            self.say(f"[plan] Codex ({self.cfg['conductors']['codex']['model']}) reviewing the plan")
            result = self.verdict(self.codex(self.root, prompt_review_plan(self), "read-only", "codex-review-plan"),
                                  "plan review")
            try:
                review.record(self.root, "plan", self.plan_id, result["verdict"], "codex", "coordinator", current,
                              result["tests"], result["findings"], max_rounds=limit)
            except review.EscalatedError as exc:
                self.state["status"] = "escalated"
                raise RunStop(str(exc))
            self.say(f"[plan] verdict: {result['verdict']}")

    # ------------------------------------------------------------ tasks

    def checkout(self, wt: Path, branch: str, base_ref: str) -> None:
        if current_branch(wt) == branch:
            return
        if dirty_paths(wt):
            raise RunStop(f"{wt.name} worktree has uncommitted changes; commit or stash them, then continue")
        if rev(self.root, "refs/heads/" + branch):
            git(wt, "switch", branch)
        else:
            git(wt, "switch", "-c", branch, base_ref)

    def build(self, task: dict, owner: str, wt: Path, findings: Optional[List[str]] = None, failure: str = "") -> None:
        before = (current_branch(wt), rev(wt, "HEAD"))
        self._build(task, owner, wt, findings, failure)
        after = (current_branch(wt), rev(wt, "HEAD"))
        if after != before:  # agents may edit files, never commit or switch branches
            raise RunStop(f"[{task['id']}] the {owner} agent changed the branch or HEAD ({before[0]}@{str(before[1])[:12]} -> "
                          f"{after[0]}@{str(after[1])[:12]}). Inspect {wt} by hand; nothing was reviewed or pushed.")

    def _build(self, task: dict, owner: str, wt: Path, findings: Optional[List[str]], failure: str) -> None:
        tid = task["id"]
        if owner == "claude":
            tools = ["Read", "Edit", "Write", "Grep", "Glob", "Task", "Agent", *self.bash_allow()]
            if "research" in (task.get("subagents") or []):
                tools += ["WebSearch", "WebFetch"]
            self.say(f"[{tid}] Claude Code conductor building (subagents: implementer, tester"
                     + "".join(f", {s}" for s in task.get("subagents") or []) + ")")
            self.claude(wt, prompt_build_claude(self, task, findings, failure), tools, f"claude-build-{tid}")
            return
        notes = ""
        if "research" in (task.get("subagents") or []) and not findings and not failure:
            self.say(f"[{tid}] Codex research worker")
            notes = self.codex(wt, prompt_codex_role(self, task, "research"), "read-only", f"codex-research-{tid}", "research")
        self.say(f"[{tid}] Codex implement worker")
        self.codex(wt, prompt_codex_role(self, task, "implement", notes, findings, failure), "workspace-write",
                   f"codex-implement-{tid}", "implement")
        self.say(f"[{tid}] Codex test worker")
        self.codex(wt, prompt_codex_role(self, task, "test"), "workspace-write", f"codex-test-{tid}", "test")
        if "security" in (task.get("subagents") or []):
            self.say(f"[{tid}] Codex security worker")
            sec = self.verdict(self.codex(wt, prompt_codex_role(self, task, "security"), "read-only",
                                          f"codex-security-{tid}", "security"), "security review")
            if sec["verdict"] == "NO-GO":
                self.say(f"[{tid}] Codex implement worker fixing {len(sec['findings'])} security finding(s)")
                self.codex(wt, prompt_codex_role(self, task, "implement", findings=sec["findings"]), "workspace-write",
                           f"codex-implement-security-{tid}", "implement")

    def run_tests(self, wt: Path) -> tuple:
        with self.unlocked():
            code, out, err = run_tree(self.test_command(), wt, int(self.opts["test_timeout_minutes"] * 60), shell=True)
        return (124 if code is None else code), ((out or "") + (err or ""))[-6000:]

    def verify_and_commit(self, task: dict, owner: str, wt: Path, st: Dict) -> bool:
        tid, attempts = task["id"], 0
        while True:
            changed = dirty_paths(wt)
            protected = [p for p in changed if p.strip('"').startswith(PROTECTED)]
            if protected:
                raise RunStop(f"[{tid}] the agent changed protected paths: {', '.join(protected)}. "
                              f"Revert them in {wt}, then: ai-eng run --continue {self.plan_id}")
            if changed:
                code, output = self.run_tests(wt)
                st["last_tests"] = f"{self.test_command()}: exit {code} (run by ai-eng)"
                self.save()
                if code == 0:
                    git(wt, "add", "-A", "--", ".", *JUNK)
                    git(wt, "commit", "-q", "-m", f"{task['title']}\n\nPlan {self.plan_id}, task {tid}, author {owner}. "
                                                  "Committed by ai-eng after its own test run passed.")
                    st.update(sha=rev(wt, "HEAD"), status="reviewing")
                    self.save()  # a crash from here on resumes at review, never rebuilds or re-commits
                    self.say(f"[{tid}] tests pass; committed {st['sha'][:12]}")
                    return True
                problem = f"`{self.test_command()}` failed (exit {code}):\n{output}"
            else:
                problem = "you made no file changes"
            if attempts >= int(self.opts["max_fix_attempts"]):
                self.say(f"[{tid}] stopped: {problem.splitlines()[0]} after {attempts} fix attempt(s)")
                return False
            attempts += 1
            self.say(f"[{tid}] fix attempt {attempts}: {problem.splitlines()[0]}")
            self.build(task, owner, wt, findings=st.get("findings"), failure=problem)

    def review_worktree(self, reviewer: str, sha: str) -> Path:
        """A detached checkout used only for this conductor's reviews, so it never touches the conductor's own work."""
        path = self.worktree(reviewer).parent / f"review-{reviewer}"
        if not path.exists():
            git(self.root, "worktree", "add", "--quiet", "--detach", str(path), sha)
            return path
        if dirty_paths(path):
            raise RunStop(f"{path} has uncommitted changes, but reviewers must not edit files. Inspect it, "
                          f"clean it (git -C \"{path}\" status), then: ai-eng run --continue {self.plan_id}")
        git(path, "switch", "--quiet", "--detach", sha)
        return path

    def review_commit(self, task: dict, owner: str, branch: str, base_ref: str, sha: str, tests: str) -> Dict:
        reviewer = OTHER[owner]
        rwt = self.review_worktree(reviewer, sha)
        diff = git(self.root, "diff", f"{base_ref}...{sha}") or ""
        prompt = prompt_review_commit(self, task, owner, reviewer, branch, sha, diff, tests)
        self.say(f"[{task['id']}] {reviewer} reviewing {sha[:12]}")
        if reviewer == "claude":
            text = self.claude(rwt, prompt, READ_TOOLS + self.bash_allow(), f"claude-review-{task['id']}")
        else:
            text = self.codex(rwt, prompt, "read-only", f"codex-review-{task['id']}")
        if dirty_paths(rwt):
            raise RunStop(f"[{task['id']}] the {reviewer} reviewer changed files in {rwt}; its verdict was not recorded")
        return self.verdict(text, f"{task['id']} review")

    def publish(self, task: dict, owner: str, st: Dict) -> None:
        tid, branch = task["id"], st["branch"]
        ok, message = review.check(review.load_all(self.root), "commit", branch, rev(self.root, "refs/heads/" + branch))
        if not ok:
            raise RunStop(f"[{tid}] refusing to publish: {message}")
        if not origin_url(self.root):
            st.update(status="ready", note="no 'origin' remote: push the branch yourself")
            self.say(f"[{tid}] GO, ready; no origin remote to push to")
            return
        code, out, err = run(["git", "push", "-u", "origin", branch], cwd=self.root, timeout=180)  # locked: one push at a time
        if code != 0:
            st.update(status="ready", note=f"push failed: {(err or out)[-300:]}")
            self.say(f"[{tid}] GO, but push failed: {(err or out).splitlines()[-1] if (err or out) else code}")
            return
        if shutil.which("gh") is None:
            st.update(status="ready", note="pushed; install gh to open the draft PR automatically")
            self.say(f"[{tid}] pushed {branch}; gh not installed, open the PR yourself")
            return
        body = self.log_dir / f"pr-{tid}.md"
        write_text(body, pr_body(self, task, owner, st))
        with self.unlocked():
            code, out, err = run(["gh", "pr", "create", "--draft", "--base", st["base_ref"], "--head", branch,
                                  "--title", f"{task['title']} ({self.plan_id} {tid})", "--body-file", str(body)],
                                 cwd=self.root, timeout=120)
        if code != 0:
            st.update(status="ready", note=f"pushed; PR not opened: {(err or out)[-300:]}")
            self.say(f"[{tid}] pushed {branch}; opening the PR failed")
            return
        st.update(status="published", pr_url=out.strip().splitlines()[-1] if out.strip() else "")
        self.say(f"[{tid}] draft PR: {st['pr_url']}")

    def do_task(self, task: dict) -> None:
        tid, owner = task["id"], task["owner"]
        st = self.state["tasks"].setdefault(tid, {"status": "pending", "owner": owner})
        limit = self.cfg["limits"]["max_review_rounds"]
        if st["status"] == "published":
            return
        if st["status"] == "ready":  # GO recorded, push or PR failed earlier: try again
            self.publish(task, owner, st)
            self.save()
            return
        if st["status"] == "escalated":
            hist = review.history(review.load_all(self.root), "commit", st.get("branch", ""))
            if review.nogo_count(hist) >= limit:
                self.say(f"[{tid}] still escalated ({review.nogo_count(hist)} NO-GO); raise limits.max_review_rounds or fix by hand")
                return
            st.update(status="building", findings=hist[-1]["findings"] if hist else None, built_from=None)
            self.say(f"[{tid}] resuming after escalation (limit is now {limit})")
        if st["status"] == "failed":
            st["status"] = "building"
            self.say(f"[{tid}] retrying the failed task")
        deps = task.get("depends_on") or []
        unfinished = [d for d in deps if self.state["tasks"].get(d, {}).get("status") not in DONE]
        if unfinished:
            st["status"] = "blocked"
            self.say(f"[{tid}] blocked: waiting for {', '.join(unfinished)}")
            return
        dep_branch = self.state["tasks"][deps[0]]["branch"] if len(deps) == 1 else None
        st.setdefault("branch", f"{owner}/{self.plan_id}-{tid.lower()}")
        st.setdefault("base_ref", dep_branch or self.cfg["project"]["base_branch"])
        wt = self.worktree(owner)
        self.checkout(wt, st["branch"], st["base_ref"])
        reviewer = OTHER[owner]
        while True:
            if st["status"] in ("pending", "blocked", "building"):
                st["status"] = "building"
                head = rev(wt, "HEAD")
                if st.get("built_from") and head != st["built_from"] and not dirty_paths(wt):
                    st["status"] = "reviewing"  # crashed after ai-eng's commit: review it, do not rebuild
                else:
                    if not dirty_paths(wt):  # dirty means an interrupted build: test and fix it instead
                        st["built_from"] = head
                        self.save()
                        self.build(task, owner, wt, findings=st.get("findings"))
                    if not self.verify_and_commit(task, owner, wt, st):
                        st["status"] = "failed"
                        self.save()
                        return
            sha = rev(self.root, "refs/heads/" + st["branch"])
            hist = review.history(review.load_all(self.root), "commit", st["branch"])
            if hist and hist[-1].get("sha") == sha:  # already reviewed this exact commit (crash after recording)
                result = {"verdict": hist[-1]["verdict"], "findings": hist[-1]["findings"]}
            else:
                result = self.review_commit(task, owner, st["branch"], st["base_ref"], sha, st.get("last_tests", ""))
                try:
                    review.record(self.root, "commit", st["branch"], result["verdict"], reviewer, owner, sha,
                                  [*result["tests"], st.get("last_tests", "")], result["findings"], max_rounds=limit)
                except review.EscalatedError as exc:
                    st["status"] = "escalated"
                    self.say(f"[{tid}] {exc}")
                    return
                hist = review.history(review.load_all(self.root), "commit", st["branch"])
                self.say(f"[{tid}] {reviewer} verdict: {result['verdict']} (round {len(hist)})")
            if result["verdict"] == "GO":
                st.pop("findings", None)
                self.publish(task, owner, st)
                self.save()
                return
            if review.nogo_count(hist) >= limit:
                st["status"] = "escalated"
                self.say(f"[{tid}] ESCALATE: {limit} NO-GO rounds. You decide. Last findings: " + "; ".join(result["findings"]))
                return
            st.update(findings=result["findings"], status="building", built_from=None)
            self.save()

    # ------------------------------------------------------------ lanes

    def lane(self, owner: str, tasks: List[dict], owners: Dict[str, str]) -> None:
        """Build this conductor's tasks in order. Runs in its own thread, in parallel with the other conductor."""
        with self.lock:
            try:
                for task in tasks:
                    for dep in task.get("depends_on") or []:  # wait until the other lane has settled the dependency
                        while dep not in self.finished and owners[dep] not in self.lanes_done:
                            self.cond.wait(30)
                    self.do_task(task)
                    self.finished.add(task["id"])
                    self.cond.notify_all()
            except RunStop as exc:
                self.errors.append(exc)
                self.say(f"[{owner}] stopped: {exc}")
            except Exception as exc:  # report it like a stop so the other lane's work is still saved
                self.errors.append(RunStop(f"{owner} lane failed unexpectedly: {exc!r}"))
                self.say(f"[{owner}] failed unexpectedly: {exc!r}")
            finally:
                self.lanes_done.add(owner)
                self.cond.notify_all()

    def build_all(self) -> None:
        tasks = self.tasks()
        owners = {t["id"]: t["owner"] for t in tasks}
        threads = [threading.Thread(target=self.lane, args=(o, [t for t in tasks if t["owner"] == o], owners))
                   for o in OTHER if any(t["owner"] == o for t in tasks)]
        with self.unlocked():
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()


# ------------------------------------------------------------ entry points

def _preflight(root: Path, cfg: Dict, full: bool) -> None:
    if review.main_checkout(root) != root:
        raise RunStop(f"run ai-eng run from the main checkout ({review.main_checkout(root)}), not a conductor worktree", 1)
    missing = [tool for tool in ("claude", "codex") if shutil.which(tool) is None]
    if missing:
        raise RunStop(f"not installed or not on PATH: {', '.join(missing)}", 1)
    problem = preflight(root)
    if problem:
        raise RunStop(problem, 1)
    if not (cfg["project"].get("test_command") or "").strip():
        raise RunStop("set project.test_command in .ai-eng/config.json: ai-eng runs it before every commit", 1)
    if full:
        steps, problems = plan_worktrees(root, cfg)
        if problems or any(s["status"] != "exists" for s in steps):
            raise RunStop("conductor worktrees are not ready: run `ai-eng worktrees` (and `ai-eng doctor`)", 1)


@contextmanager
def _driving(run_: Run):
    """Hold the run lock and an Orca mailbox for the duration of start/resume."""
    run_.mail = Mailbox()
    try:
        with run_.lock:
            yield
    finally:
        run_.mail.stop()


def start(root: Path, cfg: Dict, idea: str, plan_id: Optional[str]) -> int:
    _preflight(root, cfg, full=True)
    run_ = Run(root, cfg, plan_id or slugify(idea))
    plan_exists = (root / run_.plan_rel).exists()
    if run_.state is not None and not (run_.state["status"] == "planning" and not plan_exists):  # a failed start may retry
        raise RunStop(f"run '{run_.plan_id}' already exists: ai-eng run --continue {run_.plan_id}, or pass another --plan-id", 1)
    if plan_exists:
        raise RunStop(f"{run_.plan_rel} already exists; pass another --plan-id", 1)
    run_.state = {"plan_id": run_.plan_id, "idea": idea, "status": "planning", "tasks": {}, "events": []}
    with _driving(run_):
        run_.say(f"[plan] coordinator ({cfg['coordinator']['model']}) writing {run_.plan_rel}")
        run_.write_plan(prompt_plan(run_, idea), "coordinator-plan")
        run_.state["status"] = "owner-edit"
        run_.say(f"[plan] written: {run_.plan_rel}")
    problems = validate_tasks(extract_json(run_.plan_text(), "tasks"))
    print(f"\nYour turn: edit {run_.plan_rel} until it is what you want"
          + (f" (also fix the task list: {'; '.join(problems)})" if problems else "")
          + f".\nThen run:  ai-eng run --continue {run_.plan_id}\n"
          "From there everything is automatic until draft PRs are open for you to merge.")
    return 0


def resume(root: Path, cfg: Dict, plan_id: str) -> int:
    run_ = Run(root, cfg, plan_id)
    if run_.state is None:
        raise RunStop(f"no run named '{plan_id}' (see: ai-eng run --status)", 1)
    if run_.state["status"] == "planning":
        if not (root / run_.plan_rel).exists():
            raise RunStop(f"the plan was never written; start again: ai-eng run \"{run_.state['idea']}\" --plan-id {plan_id}", 1)
        run_.state["status"] = "owner-edit"  # the coordinator wrote it before stopping: the owner reviews it first
        run_.say(f"[plan] {run_.plan_rel} exists from an interrupted start. Edit it, then: ai-eng run --continue {plan_id}")
        return 0
    _preflight(root, cfg, full=True)
    with _driving(run_):
        if run_.state["status"] in ("owner-edit", "plan-review", "escalated"):
            run_.plan_review_loop()
        run_.state["status"] = "building"
        run_.save()
        run_.build_all()
        statuses = {t: s["status"] for t, s in run_.state["tasks"].items()}
        run_.state["status"] = "done" if not run_.errors and all(s in DONE for s in statuses.values()) else "needs-owner"
        run_.save()
        print("\n" + summary(run_))
        if run_.errors:
            raise run_.errors[0]
    return 0 if run_.state["status"] == "done" else 2


def summary(run_: Run) -> str:
    st = run_.state
    lines = [f"Run {st['plan_id']}: {st['status']}"]
    for tid, t in st.get("tasks", {}).items():
        extra = t.get("pr_url") or t.get("note") or ""
        lines.append(f"  {tid:<6} {t.get('owner', ''):<7} {t['status']:<10} {t.get('branch', '')}  {extra}")
    tasks = st.get("tasks", {}).values()
    if st["status"] == "done" and all(t["status"] == "published" for t in tasks):
        lines.append("All tasks have draft PRs. Review and merge them on GitHub.")
    elif st["status"] == "done":
        lines.append("All tasks passed review. Tasks marked 'ready' still need a push and PR (see the note on each).")
    elif st["status"] == "needs-owner":
        lines.append(f"Some tasks need you (escalated, failed or blocked). Logs: {run_.log_dir}")
    return "\n".join(lines)


def status(root: Path, cfg: Dict, plan_id: Optional[str]) -> int:
    runs = common_dir(root) / "ai-eng" / "runs"
    ids = [plan_id] if plan_id else sorted(p.stem for p in runs.glob("*.json")) if runs.exists() else []
    if not ids:
        print("No runs. Start one: ai-eng run \"<feature idea>\"")
    for pid in ids:
        run_ = Run(root, cfg, pid)
        if run_.state is None:
            print(f"No run named '{pid}'.")
            return 1
        print(summary(run_))
        print("  last events:\n" + "\n".join(f"    {e}" for e in run_.state.get("events", [])[-5:]))
    return 0


# ------------------------------------------------------------ prompts

def _task_block(task: dict) -> str:
    return json.dumps(task, indent=2)


def prompt_plan(run_: Run, idea: str) -> str:
    return f"""AI-ENG-ROLE: plan
AI-ENG-PLAN-PATH: {run_.plan_rel}

You are the coordinator for this repository. Read docs/ai-eng/coordinator.md and docs/ENGINEERING.md first.

Feature idea from the owner:
\"\"\"{idea}\"\"\"

Read the code you need (read-only). Then write the plan to {run_.plan_rel}, following docs/ai-eng/plan-template.md.
Do not create or edit any other file. Do not ask questions; record open questions in the plan's "Risks and open questions".

The plan MUST end with a fenced ```json block in exactly this shape:
{{"tasks": [{{"id": "T1", "title": "...", "owner": "claude", "files": ["path"], "acceptance": ["checkable statement"], "subagents": [], "depends_on": []}}]}}
- owner: "claude" (architecture, frontend, integration, UX) or "codex" (backend, data, reliability, tests).
- subagents: add "research" and/or "security" only when the task needs them; implement and test always run.
- Keep tasks small, give each one owner, and never let two tasks edit the same file.
- Acceptance criteria must be checkable by the test command: {run_.test_command()}

Reply with a two-sentence summary when the file is written."""


def prompt_revise_plan(run_: Run, findings: List[str]) -> str:
    items = "\n".join(f"- {f}" for f in findings)
    return f"""AI-ENG-ROLE: revise-plan
AI-ENG-PLAN-PATH: {run_.plan_rel}

You are the coordinator. Codex reviewed {run_.plan_rel} and gave NO-GO with these findings:
{items}

Edit {run_.plan_rel} to address every finding. Keep the closing ```json task block valid. Edit no other file.
Reply with one line per finding saying how you addressed it."""


def prompt_review_plan(run_: Run) -> str:
    return f"""AI-ENG-ROLE: review-plan

You are Conductor B (Codex), independently reviewing the coordinator's plan. Do not edit any file.
Check: every task has one owner, listed files, interfaces and checkable acceptance criteria; tasks do not edit the
same files (or their order is explicit); the architecture fits the existing code; security and research needs are
flagged where they matter. Use NO-GO only for problems that must change before building.

Plan ({run_.plan_rel}):
-----
{run_.plan_text()}
-----

{VERDICT_FORMAT}"""


def _fix_section(findings: Optional[List[str]], failure: str) -> str:
    out = ""
    if findings:
        out += "\nThe other conductor reviewed your last commit and gave NO-GO. Fix every finding:\n" + "\n".join(f"- {f}" for f in findings) + "\n"
    if failure:
        out += f"\nai-eng's own check failed. Fix it:\n{failure}\n"
    return out


def prompt_build_claude(run_: Run, task: dict, findings: Optional[List[str]], failure: str) -> str:
    extra = [{"research": "`researcher`", "security": "`security-reviewer`"}[s] for s in task.get("subagents") or []]
    return f"""AI-ENG-ROLE: build
AI-ENG-TASK: {task['id']}

You are Conductor A (Claude Code), working in this worktree on the current branch. Implement task {task['id']} of plan {run_.plan_id}.

Task:
{_task_block(task)}

Full plan for context:
-----
{run_.plan_text()}
-----
{_fix_section(findings, failure)}
How to work:
- Use your subagents: `implementer` writes the code, `tester` writes and runs the tests{''.join(', ' + e for e in extra)}.
- Change only this task's files and their tests. Do not commit, push, or switch branches: ai-eng runs `{run_.test_command()}` itself, commits, and sends the commit to Codex for review.
- Finish only when `{run_.test_command()}` passes.

Reply with a short summary of what changed."""


def prompt_codex_role(run_: Run, task: dict, role: str, notes: str = "", findings: Optional[List[str]] = None, failure: str = "") -> str:
    head = f"AI-ENG-ROLE: {role}\nAI-ENG-TASK: {task['id']}\n\nTask {task['id']} of plan {run_.plan_id}:\n{_task_block(task)}\n"
    if role == "research":
        return head + "\nYou are Conductor B's research worker. Do not edit files. Find what the implementer needs to know " \
                      "(APIs, existing code to reuse, pitfalls). Cite files and URLs. Reply in under 300 words."
    if role == "implement":
        return head + (f"\nResearch notes:\n{notes}\n" if notes else "") + _fix_section(findings, failure) + \
            f"\nYou are Conductor B's implementation worker. Implement the task. Change only its files. " \
            f"Do not commit or switch branches. The test command is `{run_.test_command()}`. Reply with a short summary."
    if role == "test":
        return head + f"\nYou are Conductor B's test worker. Write or extend tests covering every acceptance criterion, run " \
                      f"`{run_.test_command()}`, and fix failures (in tests, or in the code if it is wrong). Never delete or " \
                      "weaken existing tests. Do not commit. Reply with the test results."
    return head + "\nYou are Conductor B's security worker. Do not edit files. Review the uncommitted changes (`git diff`, " \
                  "`git status`) for secrets, injection, unsafe deserialization, path traversal, auth gaps, risky new " \
                  f"dependencies.\n\n{VERDICT_FORMAT}"


def prompt_review_commit(run_: Run, task: dict, owner: str, reviewer: str, branch: str, sha: str, diff: str, tests: str) -> str:
    name = {"claude": "Conductor A (Claude Code)", "codex": "Conductor B (Codex)"}
    if len(diff) > 60000:
        diff = diff[:60000] + "\n[diff truncated; inspect the checked-out files]"
    return f"""AI-ENG-ROLE: review-commit
AI-ENG-TASK: {task['id']}

You are {name[reviewer]}, independently reviewing a commit by {name[owner]}. You are not the author.
This worktree is checked out (detached) at the commit under review: {sha} (branch {branch}). Do not edit files.

Task:
{_task_block(task)}

ai-eng's own test run on this commit: {tests}

Diff against the task's base:
-----
{diff}
-----

Check: every acceptance criterion is met; tests are real and pass (run `{run_.test_command()}` if you can); no secrets,
unrelated changes, weakened tests or disabled checks. Use NO-GO only for problems that must change before merge.

{VERDICT_FORMAT}"""


def pr_body(run_: Run, task: dict, owner: str, st: Dict) -> str:
    rec = review.history(review.load_all(run_.root), "commit", st["branch"])[-1]
    tests = "\n".join(f"- {t}" for t in rec.get("tests", []) if t) or "- (none recorded)"
    criteria = "\n".join(f"- {c}" for c in task.get("acceptance") or []) or "- (see plan)"
    return f"""## Summary

{task['title']}

## Plan and task

- Plan: `{run_.plan_rel}` (in the main checkout)
- Task: {task['id']}
- Author conductor: {owner}

## Acceptance criteria

{criteria}

## Tests run

{tests}

## Independent review

- Reviewer: {rec['reviewer']}
- Verdict: {rec['verdict']} (round {rec['round']})
- Reviewed SHA: `{rec['sha']}` (must equal this PR's head commit)

## Owner merge checklist

- [ ] The head SHA matches the reviewed SHA
- [ ] CI is green
- [ ] Scope matches the approved plan

Opened as a draft by `ai-eng run`. Only the owner merges.
"""
