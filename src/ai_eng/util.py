"""Small helpers shared by all commands."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


def run(args: List[str], cwd: Optional[Path] = None, timeout: int = 20) -> Tuple[int, str, str]:
    """Run a command without a shell. Returns (code, stdout, stderr) and never raises."""
    exe = shutil.which(args[0])
    if exe is None:
        return 127, "", f"{args[0]}: not found"
    try:
        proc = subprocess.run(
            [exe, *args[1:]],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 126, "", str(exc)
    # rstrip only: leading spaces are meaningful in `git status --porcelain`.
    return proc.returncode, proc.stdout.rstrip(), proc.stderr.strip()


def digest(text: str) -> str:
    """Hash text with line endings normalised, so Git autocrlf on Windows is not seen as an edit."""
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def read_text(path: Path) -> Optional[str]:
    """Read UTF-8 text keeping line endings as they are on disk (CRLF stays CRLF). None if missing."""
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def eol_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def with_eol(text: str, eol: str) -> str:
    text = text.replace("\r\n", "\n")
    return text.replace("\n", eol) if eol != "\n" else text


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


_SECRET_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]
# Greedy up to the last "@" before the path, so passwords containing "@" are removed whole.
_URL_CREDENTIALS = re.compile(r"(https?://)[^/\s]*@")


def redact(text: str) -> str:
    """Remove common secret formats, URL credentials and the home directory path."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = _URL_CREDENTIALS.sub(r"\1", text)
    home = str(Path.home())
    if len(home) > 1:
        for form in {home, home.replace("\\", "/")}:
            # Only whole path components: /home/a must not turn /home/ab into ~b.
            text = re.sub(re.escape(form) + r"(?![^\\/\s\"'`)\]|])", "~", text)
    return text
