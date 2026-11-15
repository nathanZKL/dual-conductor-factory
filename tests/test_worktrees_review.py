from pathlib import Path

from conftest import git, init_and_commit, snapshot


def commit_on(root: Path, branch: str, name: str):
    git(root, "switch", "-q", "-c", branch)
    (root / name).write_text(name + "\n")
    git(root, "add", name)
    git(root, "commit", "-q", "-m", name)
    sha = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "main")
    return sha


def test_worktrees_blocked_until_files_committed(ai, repo):
    ai(repo, "init", "--yes")
    code, out = ai(repo, "worktrees", "--yes")
    assert code == 2
    assert "commit the ai-eng files" in out


def test_worktrees_created_isolated_and_idempotent(ai, repo):
    init_and_commit(ai, repo)
    main_files = snapshot(repo)
    code, out = ai(repo, "worktrees", "--yes")
    assert code == 0, out
    claude = repo.parent / "proj.worktrees/claude"
    codex = repo.parent / "proj.worktrees/codex"
    assert git(claude, "branch", "--show-current") == "conductor/claude"
    assert git(codex, "branch", "--show-current") == "conductor/codex"
    # Same repository, separate checkouts.
    assert git(claude, "rev-parse", "--git-common-dir") and (claude / "CLAUDE.md").is_file()
    assert Path(git(claude, "rev-parse", "--show-toplevel")).resolve() != Path(git(codex, "rev-parse", "--show-toplevel")).resolve()
    (claude / "only-in-claude.txt").write_text("x")
    assert not (codex / "only-in-claude.txt").exists()
    # Main checkout files are untouched (only .git metadata may change).
    after = {k: v for k, v in snapshot(repo).items() if not k.startswith(".git/") and k != ".git"}
    assert after == {k: v for k, v in main_files.items() if not k.startswith(".git/") and k != ".git"}
    code, out = ai(repo, "worktrees", "--yes")
    assert code == 0 and "already exist" in out
    # A conductor working on a task branch is still a healthy worktree.
    git(claude, "switch", "-q", "-c", "claude/p1-t1")
    code, out = ai(repo, "worktrees", "--yes")
    assert code == 0 and "already exist" in out


def test_worktrees_refuse_existing_folder_and_checked_out_branch(ai, repo):
    init_and_commit(ai, repo)
    target = repo.parent / "proj.worktrees/claude"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("mine")
    git(repo, "worktree", "add", "-q", "-b", "conductor/codex", str(repo.parent / "elsewhere"))
    code, out = ai(repo, "worktrees", "--yes")
    assert code == 2
    assert "not a worktree of this repository" in out
    assert "already checked out" in out
    assert (target / "keep.txt").read_text() == "mine"


def test_worktrees_dry_run(ai, repo):
    init_and_commit(ai, repo)
    before = snapshot(repo)
    code, out = ai(repo, "worktrees", "--dry-run")
    assert code == 0 and "git worktree add" in out
    assert snapshot(repo) == before
    assert not (repo.parent / "proj.worktrees").exists()


def test_self_review_is_refused(ai, repo):
    init_and_commit(ai, repo)
    commit_on(repo, "claude/p1-t1", "a.txt")
    code, out = ai(repo, "review", "record", "--branch", "claude/p1-t1", "--verdict", "GO", "--reviewer", "claude", "--author", "Claude")
    assert code == 1
    assert "self-review is not allowed" in out


def test_go_is_tied_to_exact_sha(ai, repo):
    init_and_commit(ai, repo)
    sha = commit_on(repo, "claude/p1-t1", "a.txt")
    code, out = ai(repo, "review", "record", "--branch", "claude/p1-t1", "--verdict", "GO", "--reviewer", "codex",
                   "--author", "claude", "--test", "pytest: 3 passed")
    assert code == 0, out
    assert sha in out
    code, out = ai(repo, "review", "check", "--branch", "claude/p1-t1")
    assert code == 0 and "OK" in out
    git(repo, "switch", "-q", "claude/p1-t1")
    (repo / "b.txt").write_text("b")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "more")
    git(repo, "switch", "-q", "main")
    code, out = ai(repo, "review", "check", "--branch", "claude/p1-t1")
    assert code == 1 and "new commits need a new review" in out


def test_nogo_needs_findings_and_escalates_after_limit(ai, repo):
    init_and_commit(ai, repo)
    commit_on(repo, "codex/p1-t2", "c.txt")
    base = ["review", "record", "--branch", "codex/p1-t2", "--verdict", "NO-GO", "--reviewer", "claude", "--author", "codex"]
    code, out = ai(repo, *base)
    assert code == 1 and "needs at least one --finding" in out
    for round_no in (1, 2):
        code, out = ai(repo, *base, "--finding", f"issue {round_no}")
        assert code == 0, out
    code, out = ai(repo, *base, "--finding", "issue 3")
    assert code == 4 and "ESCALATE" in out
    code, out = ai(repo, "review", "list")
    assert out.count("NO-GO") == 3


def test_reviews_are_shared_across_worktrees(ai, repo):
    init_and_commit(ai, repo)
    ai(repo, "worktrees", "--yes")
    sha = commit_on(repo, "claude/p1-t1", "a.txt")
    codex_tree = repo.parent / "proj.worktrees/codex"
    code, out = ai(codex_tree, "review", "record", "--branch", "claude/p1-t1", "--verdict", "GO", "--reviewer", "codex", "--author", "claude")
    assert code == 0, out
    code, out = ai(repo, "review", "check", "--branch", "claude/p1-t1")
    assert code == 0 and sha in out


def test_plan_review_detects_plan_changes(ai, repo):
    init_and_commit(ai, repo)
    plan = repo / "docs/ai-eng/plans/login.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# Plan v1\n")
    code, out = ai(repo, "review", "record", "--plan-id", "login", "--verdict", "GO", "--reviewer", "codex", "--author", "coordinator")
    assert code == 0, out
    assert ai(repo, "review", "check", "--plan-id", "login")[0] == 0
    plan.write_text("# Plan v2\n")
    code, out = ai(repo, "review", "check", "--plan-id", "login")
    assert code == 1 and "changed after its GO" in out
