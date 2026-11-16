"""End-to-end tests of `ai-eng run` with fake orca/claude/codex/gh binaries on PATH."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import git

FAKE = Path(__file__).with_name("fake_agent.py")


@pytest.fixture
def auto(tmp_path, repo, ai, monkeypatch):
    """A repo with ai-eng initialised, worktrees created, a bare origin, and fake Orca and agents on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("orca", "claude", "codex", "gh"):
        if os.name == "nt":
            (bin_dir / f"{name}.cmd").write_text(f'@"{sys.executable}" "{FAKE}" {name} %*\r\n')
        else:
            shim = bin_dir / name
            shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" {name} "$@"\n')
            shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    scenario, calls = tmp_path / "scenario.json", tmp_path / "calls.json"
    monkeypatch.setenv("FAKE_SCENARIO", str(scenario))
    monkeypatch.setenv("FAKE_CALLS", str(calls))
    monkeypatch.setenv("FAKE_REPO", str(repo.resolve()))
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_coordinator")

    (repo / "check.py").write_text("import os, sys\nsys.exit(1 if os.path.exists('BROKEN') else 0)\n")
    assert ai(repo, "init", "--yes")[0] == 0
    cfg_path = repo / ".ai-eng/config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["project"]["test_command"] = f'"{sys.executable}" check.py'
    cfg["automation"]["agent_timeout_minutes"] = 2  # a lost report fails the test fast instead of hanging CI
    cfg_path.write_text(json.dumps(cfg, indent=2))
    assert ai(repo, "init", "--yes")[0] == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "ai-eng")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    assert ai(repo, "worktrees", "--yes")[0] == 0

    class Ctx:
        root, remote = repo, origin

        @staticmethod
        def scenario(**data):
            scenario.write_text(json.dumps(data))

        @staticmethod
        def calls():
            return json.loads(calls.read_text()) if calls.exists() else []

        @staticmethod
        def state(plan_id="add-a-feature"):
            common = Path(git(repo, "rev-parse", "--git-common-dir"))
            common = common if common.is_absolute() else repo / common
            return json.loads((common / "ai-eng/runs" / f"{plan_id}.json").read_text())

    Ctx.scenario()
    return Ctx


def remote_branches(origin):
    out = subprocess.run(["git", "--git-dir", str(origin), "branch", "--format=%(refname:short)"], capture_output=True, text=True).stdout
    return set(out.split())


def test_start_writes_plan_then_waits_for_owner(ai, auto):
    code, out = ai(auto.root, "run", "Add a feature")
    assert code == 0, out
    assert "Your turn: edit docs/ai-eng/plans/add-a-feature.md" in out
    assert (auto.root / "docs/ai-eng/plans/add-a-feature.md").is_file()
    assert auto.state()["status"] == "owner-edit"
    assert [c["role"] for c in auto.calls()] == ["plan"]
    code, out = ai(auto.root, "run", "Add a feature")
    assert code == 1 and "already exists" in out


def test_full_loop_cross_reviews_and_opens_draft_prs(ai, auto):
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 0, out
    state = auto.state()
    assert state["status"] == "done"
    assert {t: s["status"] for t, s in state["tasks"].items()} == {"T1": "published", "T2": "published"}
    assert {"claude/add-a-feature-t1", "codex/add-a-feature-t2"} <= remote_branches(auto.remote)
    calls = auto.calls()
    # Each commit is reviewed by the other conductor, in that conductor's separate review worktree.
    reviews = {c["task"]: (c["agent"], c["cwd"]) for c in calls if c["role"] == "review-commit"}
    assert reviews == {"T1": ("codex", "review-codex"), "T2": ("claude", "review-claude")}
    # Claude runs with a tool allowlist and never stops to ask; the Claude reviewer cannot edit.
    claude_review = next(c for c in calls if c["agent"] == "claude" and c["role"] == "review-commit")
    assert "dontAsk" in claude_review["args"] and "Edit" not in claude_review["args"]
    # Builders work in their own worktree; Codex used its role workers incl. security for T2.
    assert any(c["agent"] == "claude" and c["role"] == "build" and c["cwd"] == "claude" for c in calls)
    assert [c["role"] for c in calls if c["agent"] == "codex" and c["task"] == "T2"] == ["implement", "test", "security"]
    # Codex workers use their configured models; reviews are read-only.
    implement = next(c for c in calls if c["role"] == "implement")
    assert "gpt-6.1-terra" in implement["args"] and "workspace-write" in implement["args"]
    assert "read-only" in next(c for c in calls if c["agent"] == "codex" and c["role"] == "review-commit")["args"]
    # Conductor worktrees stay on their task branches; main checkout only gained the plan.
    assert git(auto.root.parent / "proj.worktrees/codex", "branch", "--show-current") == "codex/add-a-feature-t2"
    assert git(auto.root, "status", "--porcelain").strip() == "?? docs/ai-eng/plans/"
    records = [json.loads(p.read_text()) for p in (auto.root / ".git/ai-eng/reviews").glob("commit-*.json")]
    assert all(r["reviewer"] != r["author"] for r in records)
    assert "All tasks have draft PRs" in out


def test_commit_nogo_then_fix_then_go(ai, auto):
    auto.scenario(verdicts_T1=["NO-GO", "GO"])
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 0, out
    builds = [c for c in auto.calls() if c["role"] == "build" and c["task"] == "T1"]
    assert len(builds) == 2
    assert "verdict: NO-GO (round 1)" in out and "verdict: GO (round 2)" in out


def test_three_nogos_escalate_without_pushing(ai, auto):
    auto.scenario(verdicts_T1=["NO-GO", "NO-GO", "NO-GO"])
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 2
    state = auto.state()
    assert state["tasks"]["T1"]["status"] == "escalated"
    assert state["tasks"]["T2"]["status"] == "published"
    assert "claude/add-a-feature-t1" not in remote_branches(auto.remote)
    assert "ESCALATE" in out


def test_plan_nogo_is_revised_by_coordinator(ai, auto):
    auto.scenario(plan_verdicts=["NO-GO", "GO"])
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 0, out
    roles = [c["role"] for c in auto.calls()]
    assert roles[:4] == ["plan", "review-plan", "revise-plan", "review-plan"]


def test_failing_tests_get_a_fix_attempt(ai, auto):
    auto.scenario(break_build={"T1": 1})
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 0, out
    assert "fix attempt 1" in out
    assert not (auto.root.parent / "proj.worktrees/claude/BROKEN").exists()


def test_refuses_outside_orca(ai, auto, monkeypatch):
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE")
    code, out = ai(auto.root, "run", "Add a feature")
    assert code == 1 and "inside Orca" in out
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_coordinator")
    monkeypatch.setenv("FAKE_REPO", "/somewhere/else")
    code, out = ai(auto.root, "run", "Add a feature")
    assert code == 1 and "orca repo add --path" in out


def test_waits_while_owner_answers_a_startup_prompt(ai, auto):
    auto.scenario(blocked_waits=2)
    code, out = ai(auto.root, "run", "Add a feature")
    assert code == 0, out
    assert out.count("is waiting for you: Claude Code asks whether you trust this folder") == 1


def test_worker_that_forgets_to_report_is_reminded_once(ai, auto):
    auto.scenario(silent="AI-ENG-ROLE: plan")
    code, out = ai(auto.root, "run", "Add a feature")
    assert code == 0, out
    log = next((auto.root / ".git/ai-eng/runs/add-a-feature").glob("*coordinator-plan.log")).read_text()
    assert "reminded the worker to send worker_done" in log


def test_new_review_worktree_waits_for_orca_discovery(ai, auto):
    auto.scenario(unknown_once="review-claude")
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 0, out


def test_cross_lane_dependency_builds_on_the_other_task(ai, auto):
    tasks = [{"id": "T1", "title": "base", "owner": "claude", "depends_on": []},
             {"id": "T2", "title": "on top", "owner": "codex", "depends_on": ["T1"]}]
    auto.scenario(tasks=tasks)
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 0, out
    state = auto.state()
    assert state["tasks"]["T2"]["base_ref"] == "claude/add-a-feature-t1"
    assert git(auto.root, "merge-base", "--is-ancestor", "claude/add-a-feature-t1", "codex/add-a-feature-t2") == ""


def test_reviewer_that_edits_is_stopped(ai, auto):
    auto.scenario(reviewer_edits="T1")
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 2 and "reviewer changed files" in out
    assert "claude/add-a-feature-t1" not in remote_branches(auto.remote)


def test_failed_task_is_retried_on_continue(ai, auto):
    auto.scenario(break_build={"T1": 3})  # initial build + 2 fix attempts all broken
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 2 and auto.state()["tasks"]["T1"]["status"] == "failed"
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 0, out
    assert "retrying the failed task" in out and auto.state()["tasks"]["T1"]["status"] == "published"


def test_escalated_task_resumes_after_owner_raises_limit(ai, auto):
    auto.scenario(verdicts_T1=["NO-GO", "NO-GO", "NO-GO", "GO"])
    ai(auto.root, "run", "Add a feature")
    assert ai(auto.root, "run", "--continue", "add-a-feature")[0] == 2
    cfg_path = auto.root / ".ai-eng/config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["limits"]["max_review_rounds"] = 4
    cfg_path.write_text(json.dumps(cfg))
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 0, out
    assert auto.state()["tasks"]["T1"]["status"] == "published"


def test_failed_pr_is_retried(ai, auto):
    auto.scenario(gh_fail_once=True)
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert auto.state()["tasks"]["T1"]["status"] == "ready"
    assert "All tasks have draft PRs" not in out
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 0 and auto.state()["tasks"]["T1"]["status"] == "published"


def test_crash_during_review_resumes_without_rebuilding(ai, auto):
    auto.scenario(fail={"review-commit:T1": 1})
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 2 and "codex-review-T1" in out
    sha = auto.state()["tasks"]["T1"]["sha"]
    assert auto.state()["tasks"]["T2"]["status"] == "published"  # the other lane finished regardless
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 0, out
    assert len([c for c in auto.calls() if c["role"] == "build" and c["task"] == "T1"]) == 1
    assert git(auto.root, "rev-parse", "claude/add-a-feature-t1") == sha


def test_agent_that_commits_is_stopped(ai, auto):
    auto.scenario(commit_in_build="T2")
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 2 and "changed the branch or HEAD" in out
    assert "codex/add-a-feature-t2" not in remote_branches(auto.remote)


def test_agent_touching_protected_files_is_stopped(ai, auto):
    auto.scenario(touch_protected="T1")
    ai(auto.root, "run", "Add a feature")
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 2 and "protected paths: CLAUDE.md" in out


def test_hung_agent_is_killed_at_timeout(ai, auto):
    cfg_path = auto.root / ".ai-eng/config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["automation"]["agent_timeout_minutes"] = 0.05
    cfg_path.write_text(json.dumps(cfg))
    auto.scenario(hang="plan")
    code, out = ai(auto.root, "run", "Add a feature")
    assert code == 2 and "timed out" in out
    auto.scenario()
    code, out = ai(auto.root, "run", "Add a feature")  # a failed start can simply be retried
    assert code == 0, out


def test_more_than_one_dependency_is_rejected(ai, auto):
    tasks = [{"id": f"T{i}", "title": f"t{i}", "owner": "claude", "depends_on": deps}
             for i, deps in ((1, []), (2, []), (3, ["T1", "T2"]))]
    auto.scenario(tasks=tasks)
    code, out = ai(auto.root, "run", "Add a feature")
    assert "at most one dependency" in out
    code, out = ai(auto.root, "run", "--continue", "add-a-feature")
    assert code == 2 and "at most one dependency" in out


def test_preflight_requires_main_checkout(ai, auto):
    code, out = ai(auto.root.parent / "proj.worktrees/claude", "run", "Add a feature")
    assert code == 1 and "main checkout" in out
