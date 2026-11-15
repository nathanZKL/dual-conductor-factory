"""Build release artifacts into dist/: ai-eng-<version>.pyz, wheel, sdist and SHA256SUMS.

Usage:
  python scripts/build_release.py            # pyz + wheel + sdist + checksums (wheel/sdist need `uv`)
  python scripts/build_release.py --pyz-only # zipapp only (stdlib, no network)
  python scripts/build_release.py --smoke    # also run the built pyz against a throwaway Git repo
  python scripts/build_release.py --notes    # print this version's CHANGELOG section
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def version() -> str:
    text = (ROOT / "src/ai_eng/__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def build_pyz(ver: str) -> Path:
    target = DIST / f"ai-eng-{ver}.pyz"
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "app"
        shutil.copytree(ROOT / "src/ai_eng", stage / "ai_eng", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        zipapp.create_archive(stage, target, interpreter="/usr/bin/env python3", main="ai_eng.cli:entry", compressed=True)
    return target


def write_checksums() -> Path:
    lines = []
    for path in sorted(DIST.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS" and not path.name.startswith("."):
            lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    out = DIST / "SHA256SUMS"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def smoke(pyz: Path, ver: str) -> None:
    out = subprocess.run([sys.executable, str(pyz), "--version"], capture_output=True, text=True, check=True).stdout
    assert ver in out, out
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "smoke"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=env)
        (repo / "pyproject.toml").write_text("\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, env=env)
        for args in (["init", "--dry-run"], ["init", "--yes"], ["doctor"]):
            proc = subprocess.run([sys.executable, str(pyz), *args, "--repo", str(repo)], capture_output=True, text=True, env=env)
            # doctor exits 1 when optional agent CLIs are missing on CI machines; it must still run.
            if proc.returncode not in (0, 1) or "Traceback" in proc.stderr:
                raise SystemExit(f"smoke failed: {args}\n{proc.stdout}\n{proc.stderr}")
        assert (repo / "CLAUDE.md").is_file() and (repo / ".github/workflows/ai-eng-ci.yml").is_file()
    print(f"smoke ok: {pyz.name}")


def notes(ver: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## \[?{re.escape(ver)}\]?.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match:
        raise SystemExit(f"CHANGELOG.md has no section for {ver}")
    return match.group(1).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyz-only", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--notes", action="store_true")
    args = parser.parse_args()
    ver = version()
    if args.notes:
        sys.stdout.write(notes(ver))
        return
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    pyz = build_pyz(ver)
    if not args.pyz_only:
        subprocess.run(["uv", "build", "--out-dir", str(DIST)], cwd=ROOT, check=True)
    sums = write_checksums()
    print(sums.read_text(encoding="utf-8"), end="")
    if args.smoke:
        smoke(pyz, ver)


if __name__ == "__main__":
    main()
