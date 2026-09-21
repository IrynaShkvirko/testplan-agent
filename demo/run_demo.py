"""Build the demo repository and generate a plan for each demo change.

    python demo/run_demo.py                  # writes demo/_repo, demo/changes/*, demo/plans/*
    python demo/run_demo.py --out /tmp/plans # write the plans somewhere else

Everything here is synthetic: a tiny made-up shop, three made-up changes, and plans written by the
rule-based baseline (no model is called). The plans are useful as examples of the output format and
as the baseline a model-written plan has to beat; they are not results on real software.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

import build_demo_repo  # noqa: E402

from testplan_agent.cli import main as testplan  # noqa: E402

AS_OF = "2026-09-01"


def run(repo: Path, changes: Path, out: Path) -> List[str]:
    """Generate one Markdown and one JSON plan per change. Returns the change names."""
    names = build_demo_repo.build(repo, changes)
    out.mkdir(parents=True, exist_ok=True)
    failed = []
    for name in names:
        code = testplan(
            [
                "generate",
                "--diff",
                str(changes / name / "change.patch"),
                "--spec",
                str(changes / name / "story.md"),
                "--repo",
                str(repo),
                "--as-of",
                AS_OF,
                "--output",
                str(out / f"{name}.md"),
                "--json",
                str(out / f"{name}.json"),
            ]
        )
        if code != 0:
            failed.append(name)
    if failed:
        raise SystemExit(f"plans with unresolved errors: {', '.join(failed)}")
    return names


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(HERE / "_repo"))
    parser.add_argument("--changes", default=str(HERE / "changes"))
    parser.add_argument("--out", default=str(HERE / "plans"))
    args = parser.parse_args(argv)
    names = run(Path(args.repo), Path(args.changes), Path(args.out))
    print(f"generated {len(names)} plans in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
