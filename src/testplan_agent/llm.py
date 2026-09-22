"""Model access behind one small interface, so the whole pipeline runs offline in tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Union

from .bundle import ContextBundle
from .prompts import extract_context_json

# One message of the conversation: {"role": "user" | "assistant", "content": text}.
Turn = Dict[str, str]


class LLMError(RuntimeError):
    """The client could not produce an answer at all (as opposed to a wrong answer).

    ``kind`` says what went wrong: "refusal" and "truncated" are answers the model gave;
    anything else ("auth", "rate_limit", "connection", ...) is a failure to get an answer.
    ``usage`` is what the failed call still cost, if anything (a refusal after partial output,
    an answer cut off at the token limit). ``spent`` is filled in by the planner: the totals of
    every call made for the plan, so the user learns what a failed run cost.
    """

    def __init__(self, message: str, usage: Optional[Usage] = None, kind: str = "error") -> None:
        super().__init__(message)
        self.usage = usage
        self.kind = kind
        self.spent: Optional[Dict[str, Any]] = None


@dataclass
class Usage:
    """What one call cost. ``None`` means unknown, which is not the same as zero."""

    model: str = ""  # the model that actually answered (a fallback may differ from the one asked)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: Optional[int] = None  # wall-clock time of the call, when a model was called
    cost_usd: Optional[float] = None  # an estimate from list prices
    stop_reason: str = ""
    request_id: str = ""


@dataclass
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)


class LLMClient(Protocol):
    name: str
    model: str
    # True when the same prompt always gives the same answer, so a repair round cannot help.
    deterministic: bool

    def complete(self, system: str, turns: Sequence[Turn]) -> Completion:
        """Answer the conversation. ``turns`` starts and ends with a user turn.

        The answer text is a JSON object, optionally in a code fence. Raise ``LLMError`` when
        no answer can be produced.
        """


Reply = Union[str, dict, Callable[[str, Sequence[Turn]], str]]


class ScriptedClient:
    """Plays back prepared answers in order; the last one repeats. For tests and offline runs."""

    name = "scripted"
    model = "scripted"
    deterministic = False  # each call plays the next reply

    def __init__(self, replies: Sequence[Reply]) -> None:
        if not replies:
            raise ValueError("ScriptedClient needs at least one reply")
        self._replies = list(replies)
        self.calls: List[dict] = []

    def complete(self, system: str, turns: Sequence[Turn]) -> Completion:
        self.calls.append({"system": system, "turns": [dict(t) for t in turns]})
        index = min(len(self.calls) - 1, len(self._replies) - 1)
        reply = self._replies[index]
        if callable(reply):
            text = reply(system, turns)
        else:
            text = reply if isinstance(reply, str) else json.dumps(reply)
        return Completion(text, Usage(model=self.model))


class HeuristicClient:
    """A rule-based planner with no model behind it.

    It reads the same prompt a model would and returns a plan built from templates. It makes the
    tool useful offline and is the baseline any real model has to beat in the evaluation.
    """

    name = "heuristic-baseline"
    model = "none (rule-based)"
    deterministic = True

    def complete(self, system: str, turns: Sequence[Turn]) -> Completion:
        from .baseline import build_baseline_plan

        raw = extract_context_json(turns[0]["content"]) if turns else None
        if raw is None:
            raise LLMError("no context bundle found in the prompt", kind="client")
        plan = build_baseline_plan(ContextBundle.from_dict(raw))
        return Completion(json.dumps(plan), Usage(model=self.model, cost_usd=0.0))
