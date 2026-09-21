"""Risk signals from git history: churn, authors and earlier bug fixes on the touched files."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

CHURN_DAYS = 90
BUGFIX_DAYS = 365
_BUGFIX = re.compile(
    r"\b(fix|fixes|fixed|bug|bugfix|hotfix|regression|revert|defect|incident|crash)\b",
    re.IGNORECASE,
)
_SEP = "\x1f"


@dataclass
class FileHistory:
    path: str
    commits_90d: int = 0
    authors_90d: int = 0
    bugfixes_365d: int = 0
    last_change: Optional[str] = None
    bugfix_subjects: List[str] = field(default_factory=list)


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=30,
            env=_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _env() -> Dict[str, str]:
    """A clean environment: no user, system or repository-supplied global git configuration.

    ``HOME`` must not point into the repository being analysed, or git would read a
    ``.gitconfig`` that the repository's author wrote.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def is_git_repo(repo: Path) -> bool:
    return (_git(repo, "rev-parse", "--is-inside-work-tree") or "").strip() == "true"


def parse_git_date(stamp: str) -> Optional[date]:
    """Date of a strict ISO 8601 git timestamp.

    Newer git writes UTC as ``Z``, which ``datetime.fromisoformat`` accepts only from Python 3.11.
    """
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(stamp).date()
    except ValueError:
        return None


def file_history(repo: Path, paths: Sequence[str], as_of: date) -> Dict[str, FileHistory]:
    """History of each path as of a date. Missing or new files get an empty history."""
    out: Dict[str, FileHistory] = {}
    since = (as_of - timedelta(days=BUGFIX_DAYS)).isoformat()
    until = (as_of + timedelta(days=1)).isoformat()
    churn_from = as_of - timedelta(days=CHURN_DAYS)
    for path in paths:
        hist = FileHistory(path=path)
        raw = _git(
            repo,
            "log",
            f"--since={since}",
            f"--until={until}",
            "--follow",
            # %cI: --since and --until filter on the commit date, so count by it too
            f"--format=%H{_SEP}%an{_SEP}%cI{_SEP}%s",
            "--",
            path,
        )
        authors = set()
        for line in (raw or "").splitlines():
            parts = line.split(_SEP)
            if len(parts) != 4:
                continue
            _, author, stamp, subject = parts
            when = parse_git_date(stamp)
            if when is None:
                continue
            if hist.last_change is None or when.isoformat() > hist.last_change:
                hist.last_change = when.isoformat()
            if when >= churn_from:
                hist.commits_90d += 1
                authors.add(author)
            if _BUGFIX.search(subject):
                hist.bugfixes_365d += 1
                if len(hist.bugfix_subjects) < 3:
                    hist.bugfix_subjects.append(subject[:80])
        hist.authors_90d = len(authors)
        out[path] = hist
    return out
