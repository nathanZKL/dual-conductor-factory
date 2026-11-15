"""Regression tests for findings from the independent review of PR #1."""
import json
from pathlib import Path

import pytest

from ai_eng import planner
from ai_eng.config import default_config
from ai_eng.util import redact
from conftest import init_and_commit, snapshot
from test_worktrees_review import commit_on


def test_plan_go_requires_plan_file_and_tracks_its_version(ai, repo):
    init_and_commit(ai, repo)
    code, out = ai(repo, "review", "record", "--plan-id", "nope", "--verdict", "GO", "--reviewer", "codex", "--author", "coordinator")
    assert code == 1 and "not found" in out
    plan = repo / "docs/ai-eng/plans/p.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("v1\n")
    assert ai(repo, "review", "record", "--plan-id", "p", "--verdict", "GO", "--reviewer", "codex", "--author", "coordinator")[0] == 0
    plan.unlink()
    code, out = ai(repo, "review", "check", "--plan-id", "p")
    assert code == 1 and "not found" in out


def test_crlf_file_keeps_crlf(ai, repo):
    (repo / "CLAUDE.md").write_bytes(b"line1\r\nline2\r\n")
    code, out = ai(repo, "init", "--yes")
    assert code == 0, out
    data = (repo / "CLAUDE.md").read_bytes()
    assert data.startswith(b"line1\r\nline2\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")
    code, out = ai(repo, "init", "--yes")
    assert code == 0 and "Nothing to change" in out


def test_non_utf8_file_is_a_conflict_not_a_crash(ai, repo):
    (repo / "AGENTS.md").write_bytes(b"\xff\xfeh\x00i\x00")
    before = snapshot(repo)
    code, out = ai(repo, "init", "--yes")
    assert code == 2 and "not UTF-8" in out and "Traceback" not in out
    assert snapshot(repo) == before
    assert ai(repo, "init", "--yes", "--on-conflict", "skip")[0] == 0
    assert (repo / "AGENTS.md").read_bytes() == b"\xff\xfeh\x00i\x00"
    code, out = ai(repo, "doctor", "--json")
    assert "Traceback" not in out
    assert any(c["id"] == "file:AGENTS.md" and c["status"] == "fail" for c in json.loads(out)["checks"])


def test_side_file_never_overwritten(ai, repo):
    agent = repo / ".claude/agents/tester.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("mine\n")
    (repo / ".claude/agents/tester.md.ai-eng-new").write_text("my merge in progress\n")
    code, out = ai(repo, "init", "--yes", "--on-conflict", "side")
    assert code == 0, out
    assert (repo / ".claude/agents/tester.md.ai-eng-new").read_text() == "my merge in progress\n"
    assert (repo / ".claude/agents/tester.md.ai-eng-new.2").is_file()


def test_apply_refuses_files_changed_after_preview(repo):
    cfg = default_config("proj", "d", "none", "main")
    (repo / "CLAUDE.md").write_text("original\n")
    actions = planner.plan(repo, cfg, {})
    (repo / "CLAUDE.md").write_text("edited during the prompt\n")
    before = snapshot(repo)
    with pytest.raises(planner.StaleError):
        planner.apply(repo, actions, "stop")
    assert snapshot(repo) == before


def test_similar_branch_names_do_not_collide(ai, repo):
    init_and_commit(ai, repo)
    commit_on(repo, "feat/x", "a.txt")
    commit_on(repo, "feat_x", "b.txt")
    for branch in ("feat/x", "feat_x"):
        code, out = ai(repo, "review", "record", "--branch", branch, "--verdict", "GO", "--reviewer", "codex", "--author", "claude")
        assert code == 0, out


def test_plan_id_traversal_and_kind_mismatch_rejected(ai, repo):
    init_and_commit(ai, repo)
    code, out = ai(repo, "review", "check", "--plan-id", "../../README")
    assert code == 1 and "invalid plan id" in out
    plan = repo / "docs/ai-eng/plans/p.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("v1\n")
    code, out = ai(repo, "review", "record", "--kind", "commit", "--plan-id", "p", "--verdict", "GO", "--reviewer", "codex", "--author", "coordinator")
    assert code == 1 and "does not match" in out


def test_redact_password_with_at_and_home_boundary(monkeypatch):
    assert redact("https://user:p@ss@host/x") == "https://host/x"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/a")))
    assert redact("/home/a/proj and /home/ab/proj") == "~/proj and /home/ab/proj"


def test_settings_non_ascii_preserved(ai, repo):
    settings = repo / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"note": "café"}, ensure_ascii=False), encoding="utf-8")
    code, out = ai(repo, "init", "--yes")
    assert code == 0, out
    assert "café" in settings.read_text(encoding="utf-8")


def test_no_record_after_escalation(ai, repo):
    init_and_commit(ai, repo)
    commit_on(repo, "codex/t", "c.txt")
    args = ["review", "record", "--branch", "codex/t", "--reviewer", "claude", "--author", "codex"]
    for n in range(3):
        ai(repo, *args, "--verdict", "NO-GO", "--finding", f"f{n}")
    code, out = ai(repo, *args, "--verdict", "GO")
    assert code == 4 and "escalated" in out
    assert ai(repo, "review", "check", "--branch", "codex/t")[0] == 1
