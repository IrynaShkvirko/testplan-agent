"""Model access behind one small interface, so the whole pipeline runs offline in tests."""

from __future__ import annotations

import json
from typing import Callable, List, Protocol, Sequence, Union

from .bundle import ContextBundle
from .prompts import extract_context_json


class LLMClient(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str) -> str:
        """Return the model's answer as text (a JSON object, optionally in a code fence)."""


Reply = Union[str, dict, Callable[[str, str], str]]


class ScriptedClient:
    """Plays back prepared answers in order; the last one repeats. For tests and offline runs."""

    name = "scripted"
    model = "scripted"

    def __init__(self, replies: Sequence[Reply]) -> None:
        if not replies:
            raise ValueError("ScriptedClient needs at least one reply")
        self._replies = list(replies)
        self.calls: List[dict] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        index = min(len(self.calls) - 1, len(self._replies) - 1)
        reply = self._replies[index]
        if callable(reply):
            return reply(system, user)
        return reply if isinstance(reply, str) else json.dumps(reply)


class HeuristicClient:
    """A rule-based planner with no model behind it.

    It reads the same prompt a model would and returns a plan built from templates. It makes the
    tool useful offline and is the baseline any real model has to beat in the evaluation.
    """

    name = "heuristic-baseline"
    model = "none (rule-based)"

    def complete(self, system: str, user: str) -> str:
        from .baseline import build_baseline_plan

        raw = extract_context_json(user)
        if raw is None:
            raise ValueError("no context bundle found in the prompt")
        return json.dumps(build_baseline_plan(ContextBundle.from_dict(raw)))
