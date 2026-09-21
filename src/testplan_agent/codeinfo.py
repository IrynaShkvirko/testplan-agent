"""Static facts about Python source: definitions, imports and module names. Never executes code."""

from __future__ import annotations

import ast
import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

_HTTP_DECORATOR = re.compile(
    r"\.(get|post|put|delete|patch|route|head|options)\(\s*['\"]([^'\"]+)['\"]", re.IGNORECASE
)
_METHODS_ARG = re.compile(r"methods\s*=\s*\[\s*['\"](\w+)['\"]", re.IGNORECASE)

SKIP_DIRS = {
    ".git",
    ".hg",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "site-packages",
}


@dataclass
class Symbol:
    qualname: str
    kind: str  # function | method | class | module
    path: str
    start: int
    end: int
    decorators: List[str] = field(default_factory=list)
    args: str = ""
    endpoint: Optional[str] = None  # e.g. "POST /refunds"

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]


def parse_python(text: str) -> Optional[ast.AST]:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _decorator_text(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return ""


def _endpoint(decorators: Iterable[str]) -> Optional[str]:
    for text in decorators:
        match = _HTTP_DECORATOR.search(text)
        if not match:
            continue
        verb, route = match.group(1).upper(), match.group(2)
        if verb == "ROUTE":
            methods = _METHODS_ARG.search(text)
            verb = methods.group(1).upper() if methods else "GET"
        return f"{verb} {route}"
    return None


def definitions(text: str, path: str) -> List[Symbol]:
    """Every function, method and class defined in ``text``, with line ranges."""
    tree = parse_python(text)
    if tree is None:
        return []
    out: List[Symbol] = []

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                decorators = [_decorator_text(d) for d in child.decorator_list]
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                out.append(
                    Symbol(
                        qualname=qual,
                        kind="method" if in_class else "function",
                        path=path,
                        start=start,
                        end=getattr(child, "end_lineno", child.lineno) or child.lineno,
                        decorators=decorators,
                        args=_args_text(child),
                        endpoint=_endpoint(decorators),
                    )
                )
                visit(child, f"{qual}.", False)
            elif isinstance(child, ast.ClassDef):
                qual = f"{prefix}{child.name}"
                decorators = [_decorator_text(d) for d in child.decorator_list]
                out.append(
                    Symbol(
                        qualname=qual,
                        kind="class",
                        path=path,
                        start=child.lineno,
                        end=getattr(child, "end_lineno", child.lineno) or child.lineno,
                        decorators=decorators,
                    )
                )
                visit(child, f"{qual}.", True)
            else:
                visit(child, prefix, in_class)

    visit(tree, "", False)
    return out


def _args_text(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        return ast.unparse(node.args)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive
        return ""


def innermost(symbols: List[Symbol], line: int) -> Optional[Symbol]:
    """The smallest definition whose range contains ``line``."""
    best: Optional[Symbol] = None
    for sym in symbols:
        if sym.start <= line <= sym.end and (
            best is None or (sym.end - sym.start) < (best.end - best.start)
        ):
            best = sym
    return best


# ---- module names and imports -------------------------------------------------------------
def module_name(path: str) -> str:
    """Dotted module name for a repo-relative path (``src/`` layouts are unwrapped)."""
    parts = Path(path).with_suffix("").parts
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_modules(text: str, path: str) -> Set[str]:
    """Absolute dotted names this file imports (relative imports are resolved)."""
    tree = parse_python(text)
    if tree is None:
        return set()
    here = module_name(path).split(".") if path else []
    is_pkg = Path(path).name == "__init__.py"
    package = here if is_pkg else here[:-1]
    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                head = ".".join(base + ([node.module] if node.module else []))
            else:
                head = node.module or ""
            if head:
                found.add(head)
            for alias in node.names:
                if alias.name != "*":
                    found.add(f"{head}.{alias.name}" if head else alias.name)
    return {m for m in found if m}


def referenced_names(node: ast.AST) -> Set[str]:
    names: Set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def iter_files(root: Path, pattern: str = "*.py") -> List[Path]:
    """Files under ``root`` matching ``pattern``, in a stable order.

    Caches and virtualenvs are pruned while walking, not filtered afterwards, so a large
    ``node_modules`` or ``.venv`` is never read (and its files never count as the project's).
    """
    found: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        found.extend(Path(dirpath) / name for name in fnmatch.filter(filenames, pattern))
    return sorted(found)


def iter_python_files(root: Path) -> List[Path]:
    """All ``.py`` files under ``root`` in a stable order, skipping caches and virtualenvs."""
    return iter_files(root, "*.py")


def read_text(path: Path, limit: int = 2_000_000) -> Optional[str]:
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def split_identifier(name: str) -> List[str]:
    """``applyDiscount_v2`` -> ['apply', 'discount', 'v2'] (lowercase)."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [p.lower() for p in re.split(r"[^A-Za-z0-9]+", spaced) if p]


def group_by_path(symbols: Iterable[Symbol]) -> Dict[str, List[Symbol]]:
    grouped: Dict[str, List[Symbol]] = {}
    for sym in symbols:
        grouped.setdefault(sym.path, []).append(sym)
    return grouped


def line_range(text: str) -> Tuple[int, int]:
    n = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    return 1, max(n, 1)
