"""Facts: the evidence every plan must cite. Collectors create them; the model never does."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class Fact:
    id: str
    kind: str
    text: str
    source: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "source": self.source,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> Fact:
        return cls(
            id=raw["id"],
            kind=raw["kind"],
            text=raw["text"],
            source=raw.get("source", ""),
            data=dict(raw.get("data") or {}),
        )


class FactStore:
    """Ordered collection of facts with stable ids F1, F2, ..."""

    def __init__(self, facts: Optional[Iterable[Fact]] = None) -> None:
        self._facts: Dict[str, Fact] = {}
        for fact in facts or []:
            self._facts[fact.id] = fact

    def add(self, kind: str, text: str, source: str, **data: Any) -> Fact:
        fact = Fact(id=f"F{len(self._facts) + 1}", kind=kind, text=text, source=source, data=data)
        self._facts[fact.id] = fact
        return fact

    def get(self, fact_id: str) -> Optional[Fact]:
        return self._facts.get(fact_id)

    def __contains__(self, fact_id: object) -> bool:
        return fact_id in self._facts

    def __len__(self) -> int:
        return len(self._facts)

    def all(self) -> List[Fact]:
        return list(self._facts.values())

    def by_kind(self, kind: str) -> List[Fact]:
        return [f for f in self._facts.values() if f.kind == kind]

    def to_list(self) -> List[Dict[str, Any]]:
        return [f.to_dict() for f in self._facts.values()]

    @classmethod
    def from_list(cls, raw: Iterable[Dict[str, Any]]) -> FactStore:
        return cls(Fact.from_dict(item) for item in raw)
