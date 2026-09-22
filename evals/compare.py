"""Reviewer edit distance: how much a person changed a generated plan before they would use it.

    python -m evals.compare plan.md edited.md            # a summary
    python -m evals.compare plan.md edited.md --json     # the same, as JSON

Works on the Markdown plan, which is what a reviewer edits. It reports, for the test conditions
section (where the work is) and for the whole plan: lines kept, changed, deleted and added, and
an edit distance from 0 (untouched) to 1 (rewritten). Conditions are also matched by id, so a
reviewer's verdict per condition is visible: kept as is, reworded, dropped, or new.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_CONDITIONS = re.compile(r"^## 3\. Test conditions\s*$(.*?)^## 4\. ", re.M | re.S)
_ROW = re.compile(r"^\|\s*(TC-\d+)\s*\|(.*)\|\s*$", re.M)


def _lines(text: str) -> List[str]:
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def line_diff(before: str, after: str) -> Dict[str, Any]:
    a, b = _lines(before), _lines(after)
    counts = {"kept": 0, "changed": 0, "deleted": 0, "added": 0}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            counts["kept"] += i2 - i1
        elif op == "replace":
            paired = min(i2 - i1, j2 - j1)
            counts["changed"] += paired
            counts["deleted"] += (i2 - i1) - paired
            counts["added"] += (j2 - j1) - paired
        elif op == "delete":
            counts["deleted"] += i2 - i1
        else:
            counts["added"] += j2 - j1
    similarity = difflib.SequenceMatcher(None, "\n".join(a), "\n".join(b), autojunk=False).ratio()
    counts["edit_distance"] = round(1 - similarity, 4)
    return counts


def conditions_section(text: str) -> str:
    match = _CONDITIONS.search(text)
    return match.group(1) if match else ""


def condition_verdicts(before: str, after: str) -> Dict[str, List[str]]:
    """Conditions by id: kept as is, reworded, dropped, or added by the reviewer."""
    rows_a = {m.group(1): m.group(2).strip() for m in _ROW.finditer(conditions_section(before))}
    rows_b = {m.group(1): m.group(2).strip() for m in _ROW.finditer(conditions_section(after))}
    return {
        "kept": sorted((k for k in rows_a if rows_b.get(k) == rows_a[k]), key=_num),
        "reworded": sorted((k for k in rows_a if k in rows_b and rows_b[k] != rows_a[k]), key=_num),
        "dropped": sorted((k for k in rows_a if k not in rows_b), key=_num),
        "added": sorted((k for k in rows_b if k not in rows_a), key=_num),
    }


def _num(ident: str) -> int:
    return int(ident.split("-")[1])


def compare(before: str, after: str) -> Dict[str, Any]:
    return {
        "conditions": line_diff(conditions_section(before), conditions_section(after)),
        "whole_plan": line_diff(before, after),
        "condition_verdicts": condition_verdicts(before, after),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals.compare", description=__doc__.splitlines()[0])
    p.add_argument("plan", type=Path, help="the generated plan (Markdown)")
    p.add_argument("edited", type=Path, help="the reviewer's edited copy")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    try:
        before, after = (
            args.plan.read_text(encoding="utf-8"),
            args.edited.read_text(encoding="utf-8"),
        )
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = compare(before, after)
    if not conditions_section(before):
        print("warning: no '## 3. Test conditions' section in the plan", file=sys.stderr)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    for name in ("conditions", "whole_plan"):
        r = result[name]
        print(f"{name.replace('_', ' ')}: edit distance {r['edit_distance']:.2f} "
              f"({r['kept']} kept, {r['changed']} changed, {r['deleted']} deleted, "
              f"{r['added']} added lines)")  # fmt: skip
    v = result["condition_verdicts"]
    print(
        "conditions: "
        + ", ".join(f"{len(v[k])} {k}" for k in ("kept", "reworded", "dropped", "added"))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
