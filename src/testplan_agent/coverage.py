"""Optional coverage data (Cobertura XML, as written by ``coverage xml``)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional, Set


class CoverageError(ValueError):
    pass


def load_cobertura(path: Path) -> Dict[str, Dict[int, int]]:
    """filename -> {line number: hits}. Raises CoverageError on anything unexpected."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CoverageError(f"cannot read {path}: {exc}") from exc
    head = text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in head:
        raise CoverageError("refusing a coverage file that declares a DOCTYPE or entities")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise CoverageError(f"{path} is not valid XML: {exc}") from exc
    sources = [s.text.strip() for s in root.iter("source") if s.text]
    lines: Dict[str, Dict[int, int]] = {}
    for cls in root.iter("class"):
        name = cls.get("filename")
        if not name:
            continue
        hits = lines.setdefault(name, {})
        for line in cls.iter("line"):
            try:
                hits[int(line.get("number", ""))] = int(line.get("hits", "0"))
            except ValueError:
                continue
    if not lines:
        raise CoverageError(f"{path} has no per-file line data")
    # Cobertura file names are relative to <source>; keep both spellings addressable.
    for src in sources:
        for name in list(lines):
            prefixed = (Path(src) / name).as_posix()
            lines.setdefault(prefixed, lines[name])
    return lines


def uncovered(cov: Dict[str, Dict[int, int]], path: str, wanted: Set[int]) -> Optional[Set[int]]:
    """Which of ``wanted`` lines ran zero times. None if the file is not in the report.

    An exact name wins; otherwise the report name that shares the longest path with ``path``,
    so ``shop/utils.py`` is not answered with the data of some other ``utils.py``.
    """
    hits, best_rank = None, (-1, -1)
    for name, data in cov.items():
        if name == path:
            rank = (2, len(name))
        elif name.endswith("/" + path):
            rank = (1, -len(name))  # an absolute or prefixed spelling of the same file
        elif path.endswith("/" + name):
            rank = (0, len(name))  # a shorter name: the more of the path it covers, the better
        else:
            continue
        if rank > best_rank:
            hits, best_rank = data, rank
    if hits is None:
        return None
    return {n for n in wanted if n in hits and hits[n] == 0}
