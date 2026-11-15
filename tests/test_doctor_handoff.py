import json

import pytest

from ai_eng import doctor
from ai_eng.templates import ROLE_AGENT_FILES, load, render, target_files, values
from ai_eng.config import default_config
from ai_eng.util import redact
from conftest import git, init_and_commit, snapshot


@pytest.fixture(autouse=True)
def only_git_installed(monkeypatch):
    """Doctor must not depend on which agent CLIs the test machine has."""
    real = doctor.tool_version
    monkeypatch.setattr(doctor, "tool_version", lambda cmd: real(cmd) if cmd[0] == "git" else None)


def by_id(checks):
    return {c["id"]: c for c in checks}


def test_doctor_before_init(ai, repo):
    code, out = ai(repo, "doctor", "--json")
    assert code == 1
    checks = by_id(json.loads(out)["checks"])
    assert checks["config"]["status"] == "missing"
    assert checks["tool.git"]["status"] == "verified"
    assert checks["tool.claude"]["status"] == "missing"


def test_doctor_after_setup_is_read_only_and_honest(ai, repo):
    init_and_commit(ai, repo)
    ai(repo, "worktrees", "--yes")
    before = snapshot(repo)
    code, out = ai(repo, "doctor", "--json")
    assert code == 0, out
    assert snapshot(repo) == before
    checks = by_id(json.loads(out)["checks"])
    assert checks["config"]["status"] == "configured"
    assert checks["file:CLAUDE.md"]["status"] == "configured"
    assert checks["worktree.claude"]["status"] == "verified"
    assert checks["worktree.codex"]["status"] == "verified"
    # Capabilities ai-eng cannot prove are never reported as verified.
    for key in ("models", "subagents.claude", "messaging", "github"):
        assert checks[key]["status"] == "untested"
    assert all(c["status"] in doctor.STATUSES for c in checks.values())


def test_doctor_reports_drift_and_hand_edits(ai, repo):
    init_and_commit(ai, repo)
    (repo / ".claude/agents/tester.md").write_text("changed\n")
    code, out = ai(repo, "doctor", "--json")
    assert code == 1
    assert by_id(json.loads(out)["checks"])["file:.claude/agents/tester.md"]["status"] == "fail"


def test_handoff_contains_shas_reviews_and_no_secrets(ai, repo):
    init_and_commit(ai, repo)
    git(repo, "switch", "-q", "-c", "claude/p1-t1")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "a")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "main")
    ai(repo, "review", "record", "--branch", "claude/p1-t1", "--verdict", "GO", "--reviewer", "codex", "--author", "claude",
       "--test", "pytest: 1 passed")
    token = "ghp_" + "A" * 36
    code, out = ai(repo, "handoff", "-o", "-", "--note", f"blocked on deploy key {token}")
    assert code == 0, out
    assert sha in out
    assert git(repo, "rev-parse", "main") in out
    assert "GO by codex" in out and "pytest: 1 passed" in out
    assert "push `claude/p1-t1` and open a draft PR" in out
    assert token not in out and "[REDACTED]" in out


def test_handoff_flags_unreviewed_branches(ai, repo):
    init_and_commit(ai, repo)
    git(repo, "switch", "-q", "-c", "codex/p1-t2")
    (repo / "b.txt").write_text("b")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "b")
    git(repo, "switch", "-q", "main")
    code, out = ai(repo, "handoff", "-o", "-")
    assert "`codex/p1-t2` has 1 commit(s) ahead of `main` with no review record" in out


def test_redact():
    text = "https://user:secret@github.com/o/r.git sk-abcdefghijklmnopqrstu AKIAABCDEFGHIJKLMNOP"
    out = redact(text)
    assert "secret@" not in out and "sk-abc" not in out and "AKIA" not in out
    assert "https://github.com/o/r.git" in out


def test_all_templates_render_without_leftover_placeholders():
    cfg = default_config("demo", "Demo", "mixed", "main")
    for path, _, content in target_files(cfg):
        assert "@@" not in content, path
    vals = values(cfg)
    for agent in ROLE_AGENT_FILES.values():
        text = render(load(f"claude/agents/{agent}.md"), vals)
        assert text.startswith("---\nname: "), agent
    assert "gpt-6.1-terra" in vals["SUBAGENT_TABLE"] and "gpt-6-luna" in vals["SUBAGENT_TABLE"]


def test_unknown_placeholder_raises():
    with pytest.raises(KeyError):
        render("@@NOPE@@", {})
