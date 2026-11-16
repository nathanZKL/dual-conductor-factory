import json

from conftest import git, init_and_commit, snapshot

MANAGED = [
    ".ai-eng/config.json",
    ".ai-eng/manifest.json",
    ".ai-eng/.gitignore",
    "CLAUDE.md",
    "AGENTS.md",
    "docs/ENGINEERING.md",
    "docs/ai-eng/coordinator.md",
    "docs/ai-eng/plan-template.md",
    "docs/ai-eng/review-template.md",
    ".github/pull_request_template.md",
    ".claude/settings.json",
    ".claude/agents/implementer.md",
    ".claude/agents/tester.md",
    ".claude/agents/researcher.md",
    ".claude/agents/security-reviewer.md",
]


def test_dry_run_writes_nothing(ai, repo):
    (repo / "scratch.txt").write_text("untracked user file\n")
    before = snapshot(repo)
    code, out = ai(repo, "init", "--dry-run")
    assert code == 0, out
    assert "Dry run: nothing was written." in out
    assert "create" in out and "CLAUDE.md" in out
    assert snapshot(repo) == before


def test_init_creates_files_and_second_run_is_noop(ai, repo):
    code, out = ai(repo, "init", "--yes", "--description", "Demo app")
    assert code == 0, out
    for path in MANAGED:
        assert (repo / path).is_file(), path
    assert "Demo app" in (repo / "docs/ENGINEERING.md").read_text()
    before = snapshot(repo)
    code, out = ai(repo, "init", "--yes")
    assert code == 0, out
    assert "Nothing to change" in out
    assert snapshot(repo) == before


def test_noninteractive_without_yes_refuses(ai, repo):
    before = snapshot(repo)
    code, out = ai(repo, "init")
    assert code == 1
    assert "--yes" in out
    assert snapshot(repo) == before


def test_existing_instructions_are_kept_and_backed_up(ai, repo):
    original = "# My rules\n\nAlways use tabs.\n"
    (repo / "CLAUDE.md").write_text(original)
    code, out = ai(repo, "init", "--yes")
    assert code == 0, out
    text = (repo / "CLAUDE.md").read_text()
    assert text.startswith(original.rstrip("\n"))
    assert "<!-- ai-eng:begin" in text and "<!-- ai-eng:end -->" in text
    backups = list((repo / ".ai-eng/backups").rglob("CLAUDE.md"))
    assert len(backups) == 1 and backups[0].read_text() == original


def test_build_subagents_preload_the_build_skills(ai, repo):
    code, out = ai(repo, "init", "--yes")
    assert code == 0, out
    for name in ("implementer", "tester"):
        head = (repo / f".claude/agents/{name}.md").read_text().split("---")[1]
        assert ("skills:\n  - superpowers:test-driven-development\n"
                "  - superpowers:verification-before-completion\n") in head
    assert "skills:" not in (repo / ".claude/agents/researcher.md").read_text()
    # An empty build list in the config drops the field.
    cfg_path = repo / ".ai-eng/config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["skills"]["claude"]["build"] = []
    cfg_path.write_text(json.dumps(cfg))
    code, out = ai(repo, "init", "--yes")
    assert code == 0, out
    assert "skills:" not in (repo / ".claude/agents/implementer.md").read_text()


def test_conflicting_file_stops_and_writes_nothing(ai, repo):
    agent = repo / ".claude/agents/implementer.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("my own agent\n")
    before = snapshot(repo)
    code, out = ai(repo, "init", "--yes")
    assert code == 2
    assert "conflict" in out and "Nothing was written" in out
    assert snapshot(repo) == before


def test_conflict_skip_and_side(ai, repo):
    agent = repo / ".claude/agents/implementer.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("my own agent\n")
    code, out = ai(repo, "init", "--yes", "--on-conflict", "side")
    assert code == 0, out
    assert agent.read_text() == "my own agent\n"
    assert (repo / ".claude/agents/implementer.md.ai-eng-new").is_file()
    assert (repo / "CLAUDE.md").is_file()


def test_hand_edited_block_is_a_conflict_on_rerun(ai, repo):
    ai(repo, "init", "--yes")
    path = repo / "AGENTS.md"
    path.write_text(path.read_text().replace("Conductor B", "Conductor Z"))
    code, out = ai(repo, "init", "--yes")
    assert code == 2
    assert "edited by hand" in out
    assert "Conductor Z" in path.read_text()


def test_user_text_outside_block_survives_upgrade(ai, repo):
    ai(repo, "init", "--yes")
    path = repo / "CLAUDE.md"
    path.write_text("# Team notes\n\n" + path.read_text() + "\nFooter by team.\n")
    cfg_path = repo / ".ai-eng/config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["conductors"]["claude"]["model"] = "opus-next"
    cfg_path.write_text(json.dumps(cfg))
    code, out = ai(repo, "init", "--yes")
    assert code == 0, out
    text = path.read_text()
    assert text.startswith("# Team notes") and text.rstrip().endswith("Footer by team.")
    assert "opus-next" in text


def test_settings_merge_keeps_existing_and_is_idempotent(ai, repo):
    settings = repo / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls:*)"], "deny": ["Read(./secret)"]}, "theme": "dark"}))
    code, out = ai(repo, "init", "--yes")
    assert code == 0, out
    data = json.loads(settings.read_text())
    assert data["theme"] == "dark"
    assert data["permissions"]["allow"] == ["Bash(ls:*)"]
    assert data["permissions"]["deny"][0] == "Read(./secret)"
    assert "Bash(gh pr merge:*)" in data["permissions"]["deny"]
    before = settings.read_text()
    ai(repo, "init", "--yes")
    assert settings.read_text() == before


def test_invalid_settings_json_is_a_conflict(ai, repo):
    settings = repo / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{ not json")
    code, out = ai(repo, "init", "--yes")
    assert code == 2
    assert "not valid JSON" in out
    assert settings.read_text() == "{ not json"


def test_dirty_and_untracked_files_are_preserved(ai, repo):
    (repo / "README.md").write_text("edited but not committed\n")
    (repo / "notes.txt").write_text("untracked\n")
    code, out = ai(repo, "init", "--yes")
    assert code == 0, out
    assert "Uncommitted changes" in out
    assert (repo / "README.md").read_text() == "edited but not committed\n"
    assert (repo / "notes.txt").read_text() == "untracked\n"


def test_upgrade_plan_is_read_only(ai, repo):
    init_and_commit(ai, repo)
    before = snapshot(repo)
    code, out = ai(repo, "upgrade", "--plan")
    assert code == 0, out
    assert "unchanged" in out
    assert snapshot(repo) == before


def test_not_a_git_repo(ai, tmp_path):
    code, out = ai(tmp_path, "init", "--dry-run")
    assert code == 1
    assert "not inside a Git repository" in out


def test_stack_presets(ai, tmp_path):
    cases = {"python": ["pyproject.toml"], "node": ["package.json"], "mixed": ["pyproject.toml", "package.json"], "none": []}
    for stack, files in cases.items():
        root = tmp_path / stack
        root.mkdir()
        git(root, "init", "-q", "-b", "main")
        for name in files:
            (root / name).write_text("{}\n" if name.endswith(".json") else "\n")
        code, out = ai(root, "init", "--yes")
        assert code == 0, out
        workflow = root / ".github/workflows/ai-eng-ci.yml"
        if stack == "none":
            assert not workflow.exists()
            assert "no CI workflow" in out
            continue
        text = workflow.read_text()
        assert ("  python:" in text) == (stack in ("python", "mixed"))
        assert ("  node:" in text) == (stack in ("node", "mixed"))
        assert "@@" not in text
