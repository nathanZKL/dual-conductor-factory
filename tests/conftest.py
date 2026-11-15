import os
import subprocess
from pathlib import Path

import pytest

from ai_eng import cli


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    """Hermetic Git: no user/system config, fixed identity, no autocrlf surprises."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # No background `git maintenance`: it creates and deletes .git lock files while snapshots run.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "maintenance.auto")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "gc.auto")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "0")
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.com")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def ai(capsys):
    """Run the CLI in-process against `root`; returns (exit code, combined output)."""

    def _run(root: Path, *args: str):
        code = cli.main([*args, "--repo", str(root)])
        captured = capsys.readouterr()
        return code, captured.out + captured.err

    return _run


def snapshot(root: Path):
    """Every path under root (including .git) with size and mtime, to prove nothing was written."""
    out = {}
    for path in sorted(root.rglob("*")):
        try:
            stat = path.stat()
        except FileNotFoundError:  # transient git lock file vanished mid-walk
            continue
        out[path.relative_to(root).as_posix()] = (path.is_dir(), stat.st_size if path.is_file() else 0, stat.st_mtime_ns)
    return out


def init_and_commit(ai, root: Path, *extra: str):
    code, out = ai(root, "init", "--yes", *extra)
    assert code == 0, out
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "ai-eng init")
    return out
