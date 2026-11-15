"""Print the Homebrew formula for a published release, using the pyz checksum from its SHA256SUMS.

Usage: python scripts/render_formula.py <version> <path to SHA256SUMS>
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = "nathanZKL/dual-conductor-factory"
PYTHON = "python@3.13"

FORMULA = '''class AiEng < Formula
  desc "Set up and verify a dual-conductor (Claude Code + Codex) workflow in Git repos"
  homepage "https://github.com/{repo}"
  url "https://github.com/{repo}/releases/download/v{version}/ai-eng-{version}.pyz"
  version "{version}"
  sha256 "{sha}"
  license "MIT"

  depends_on "{python}"

  def install
    libexec.install "ai-eng-{version}.pyz" => "ai-eng.pyz"
    (bin/"ai-eng").write <<~SH
      #!/bin/sh
      exec "#{{formula_opt_bin("{python}")}}/python3" "#{{libexec}}/ai-eng.pyz" "$@"
    SH
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/ai-eng --version")
  end
end
'''


def main() -> None:
    version, sums = sys.argv[1], Path(sys.argv[2])
    name = f"ai-eng-{version}.pyz"
    for line in sums.read_text(encoding="utf-8").splitlines():
        sha, _, file = line.partition("  ")
        if file == name:
            sys.stdout.write(FORMULA.format(repo=REPO, version=version, sha=sha, python=PYTHON))
            return
    raise SystemExit(f"{name} not found in {sums}")


if __name__ == "__main__":
    main()
