"""Who depends on the changed code: a reverse import graph over the repository's own modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Set

from . import codeinfo
from .surface import ChangeSummary, classify_path


@dataclass
class Dependent:
    module: str
    path: str
    hops: int
    via: str


def build_graph(repo: Path) -> Dict[str, Dict[str, object]]:
    """module -> {"path": ..., "imports": set(known modules), "kind": file kind}."""
    texts: Dict[str, str] = {}
    paths: Dict[str, str] = {}
    for path in codeinfo.iter_python_files(repo):
        rel = path.relative_to(repo).as_posix()
        text = codeinfo.read_text(path)
        if text is None:
            continue
        name = codeinfo.module_name(rel)
        if name:
            texts[name] = text
            paths[name] = rel
    known = set(texts)
    graph: Dict[str, Dict[str, object]] = {}
    for name, text in texts.items():
        wanted = codeinfo.imported_modules(text, paths[name])
        edges: Set[str] = set()
        for imp in wanted:
            parts = imp.split(".")
            for i in range(len(parts), 0, -1):
                candidate = ".".join(parts[:i])
                if candidate in known and candidate != name:
                    edges.add(candidate)
                    break
        graph[name] = {"path": paths[name], "imports": edges, "kind": classify_path(paths[name])}
    return graph


def dependents(
    changes: Sequence[ChangeSummary], graph: Dict[str, Dict[str, object]], max_hops: int = 2
) -> List[Dependent]:
    """Non-test modules that import the changed modules, directly or through one more hop."""
    reverse: Dict[str, Set[str]] = {}
    for name, info in graph.items():
        for target in info["imports"]:  # type: ignore[attr-defined]
            reverse.setdefault(str(target), set()).add(name)
    changed = {
        codeinfo.module_name(c.path)
        for c in changes
        if c.kind == "source" and c.path.endswith(".py")
    }
    seen: Set[str] = set(changed)
    frontier = {m: m for m in changed}
    out: List[Dependent] = []
    for hop in range(1, max_hops + 1):
        nxt: Dict[str, str] = {}
        for mod, origin in sorted(frontier.items()):
            for dep in sorted(reverse.get(mod, ())):
                if dep in seen or graph[dep]["kind"] == "test":
                    continue
                seen.add(dep)
                nxt[dep] = origin
                out.append(Dependent(module=dep, path=str(graph[dep]["path"]), hops=hop, via=mod))
        frontier = nxt
    return out
