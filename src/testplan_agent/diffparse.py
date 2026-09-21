"""A small unified-diff parser (git or plain ``diff -u`` output). Standard library only."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")

ADDED = "added"
DELETED = "deleted"
MODIFIED = "modified"
RENAMED = "renamed"


class DiffError(ValueError):
    """The text is not a unified diff we can read."""


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    section: str = ""
    # (marker, text) where marker is " ", "+" or "-"
    lines: List[Tuple[str, str]] = field(default_factory=list)

    def added_lines(self) -> List[Tuple[int, str]]:
        """(line number in the new file, text) for every added line."""
        out, n = [], self.new_start
        for marker, text in self.lines:
            if marker == "+":
                out.append((n, text))
                n += 1
            elif marker == " ":
                n += 1
        return out

    def removed_lines(self) -> List[Tuple[int, str]]:
        """(line number in the old file, text) for every removed line."""
        out, n = [], self.old_start
        for marker, text in self.lines:
            if marker == "-":
                out.append((n, text))
                n += 1
            elif marker == " ":
                n += 1
        return out


@dataclass
class FileChange:
    path: str
    old_path: Optional[str] = None
    status: str = MODIFIED
    binary: bool = False
    hunks: List[Hunk] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(len(h.added_lines()) for h in self.hunks)

    @property
    def removed(self) -> int:
        return sum(len(h.removed_lines()) for h in self.hunks)

    def added_line_numbers(self) -> List[int]:
        return [n for h in self.hunks for n, _ in h.added_lines()]

    def removed_line_numbers(self) -> List[int]:
        return [n for h in self.hunks for n, _ in h.removed_lines()]

    def touched_new_lines(self) -> List[int]:
        """Lines in the new file that were added, plus the line after each pure deletion."""
        touched = set(self.added_line_numbers())
        for hunk in self.hunks:
            n = hunk.new_start
            for marker, _ in hunk.lines:
                if marker == "-":
                    touched.add(max(n, 1))
                elif marker in (" ", "+"):
                    n += 1
        return sorted(touched)

    def hunk_text(self) -> str:
        """Every line shown in the diff: added, removed and the context around them."""
        return "\n".join(t for h in self.hunks for _, t in h.lines)

    def added_text(self) -> str:
        return "\n".join(t for h in self.hunks for _, t in h.added_lines())

    def removed_text(self) -> str:
        return "\n".join(t for h in self.hunks for _, t in h.removed_lines())


def _strip_prefix(path: str) -> str:
    path = path.strip().split("\t")[0]
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def parse_diff(text: str) -> List[FileChange]:
    """Parse a unified diff into file changes. Raises DiffError if nothing can be read."""
    lines = text.splitlines()
    changes: List[FileChange] = []
    current: Optional[FileChange] = None
    i = 0
    n = len(lines)

    def start(path: str, old: Optional[str] = None) -> FileChange:
        change = FileChange(path=path, old_path=old)
        changes.append(change)
        return change

    while i < n:
        line = lines[i]
        if line.startswith("diff --git "):
            match = re.match(r"^diff --git (\S+) (\S+)$", line)
            old = _strip_prefix(match.group(1)) if match else None
            new = _strip_prefix(match.group(2)) if match else line[len("diff --git ") :]
            current = start(new, old)
            i += 1
            continue
        if current is not None and not current.hunks:
            if line.startswith("new file mode"):
                current.status = ADDED
            elif line.startswith("deleted file mode"):
                current.status = DELETED
            elif line.startswith("rename from "):
                current.old_path = line[len("rename from ") :].strip()
                current.status = RENAMED
            elif line.startswith("rename to "):
                current.path = line[len("rename to ") :].strip()
            elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
                current.binary = True
        if line.startswith("--- ") and i + 1 < n and lines[i + 1].startswith("+++ "):
            old_raw, new_raw = line[4:], lines[i + 1][4:]
            if current is None or current.hunks:
                current = start(_strip_prefix(new_raw), _strip_prefix(old_raw))
            if old_raw.strip().startswith("/dev/null"):
                current.status = ADDED
                current.old_path = None
            elif new_raw.strip().startswith("/dev/null"):
                current.status = DELETED
                current.path = _strip_prefix(old_raw)
            elif current.status not in (RENAMED,):
                current.path = _strip_prefix(new_raw)
            i += 2
            continue
        match = _HUNK.match(line)
        if match and current is not None:
            old_len = int(match.group(2)) if match.group(2) is not None else 1
            new_len = int(match.group(4)) if match.group(4) is not None else 1
            hunk = Hunk(
                old_start=int(match.group(1)),
                old_len=old_len,
                new_start=int(match.group(3)),
                new_len=new_len,
                section=match.group(5).strip(),
            )
            i += 1
            seen_old = seen_new = 0
            while i < n and (seen_old < old_len or seen_new < new_len):
                body = lines[i]
                if body.startswith("\\"):  # "\ No newline at end of file"
                    i += 1
                    continue
                marker, text_ = (body[:1] or " "), body[1:]
                if marker == "+":
                    seen_new += 1
                elif marker == "-":
                    seen_old += 1
                elif marker == " ":
                    seen_old += 1
                    seen_new += 1
                else:
                    break
                hunk.lines.append((marker, text_))
                i += 1
            while i < n and lines[i].startswith("\\"):
                i += 1
            current.hunks.append(hunk)
            continue
        i += 1

    changes = [c for c in changes if c.hunks or c.binary or c.status in (ADDED, DELETED, RENAMED)]
    if not changes:
        raise DiffError("no file changes found; expected a unified diff")
    return changes
