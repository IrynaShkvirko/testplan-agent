"""The Anthropic client: Claude drafts the plan; the validators decide whether it holds.

Needs the optional SDK (``pip install "testplan-agent[anthropic]"``), imported only when this
client is used, so the rest of the tool keeps no runtime dependencies.

Credentials come from wherever the SDK finds them: ``ANTHROPIC_API_KEY``, or a profile from
``ant auth login``. This module never reads, prints or stores a key.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from .llm import Completion, LLMError, Turn, Usage
from .pricing import estimate_cost
from .schema import model_schema

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "high"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Room for adaptive thinking plus a long plan. The answer is streamed, so a large limit
# cannot time out the request.
DEFAULT_MAX_TOKENS = 64_000
# On a policy decline the API re-runs the request on a fallback model chosen by the kind of
# refusal, inside the same call. The header gates the "default" form of the parameter.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
INSTALL_HINT = 'the Anthropic client needs the SDK: pip install "testplan-agent[anthropic]"'


class AnthropicClient:
    """Plans with Claude through the Messages API, behind the ``LLMClient`` interface."""

    name = "anthropic"
    deterministic = False

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        fallback: bool = True,
        sdk_client: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if effort not in EFFORTS:
            raise ValueError(f"effort must be one of {', '.join(EFFORTS)}")
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallback = fallback
        self._clock = clock
        self._anthropic = _import_sdk()
        if sdk_client is None:
            try:
                sdk_client = self._anthropic.Anthropic()
            except self._anthropic.AnthropicError as exc:  # e.g. no credentials found
                raise LLMError(f"the Anthropic client could not start: {exc}") from exc
        self._sdk = sdk_client
        # The API supports a subset of JSON Schema. The SDK moves the rest (lengths, ranges,
        # patterns) into descriptions, so the model still reads them; the validators enforce them.
        self._schema = self._anthropic.transform_schema(model_schema())

    def request(self, system: str, turns: Sequence[Turn]) -> Dict[str, Any]:
        """The exact parameters sent to ``client.beta.messages.stream``."""
        params: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": t["role"], "content": t["content"]} for t in turns],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": self._schema},
            },
            # Caches the conversation so far. Repairs only ever append turns, so each repair
            # reads the context bundle and earlier answers from this cache.
            "cache_control": {"type": "ephemeral"},
        }
        if self.fallback:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def complete(self, system: str, turns: Sequence[Turn]) -> Completion:
        sdk = self._anthropic
        started = self._clock()
        try:
            with self._sdk.beta.messages.stream(**self.request(system, turns)) as stream:
                message = stream.get_final_message()
                request_id = stream.request_id or ""
        except sdk.AuthenticationError as exc:
            raise LLMError(
                "no valid Anthropic credentials: set ANTHROPIC_API_KEY or run `ant auth login`"
                + _request_suffix(exc)
            ) from exc
        except sdk.PermissionDeniedError as exc:
            raise LLMError(
                f"these credentials may not use {self.model}" + _request_suffix(exc)
            ) from exc
        except sdk.NotFoundError as exc:
            raise LLMError(f"unknown model {self.model!r}" + _request_suffix(exc)) from exc
        except sdk.BadRequestError as exc:
            raise LLMError(
                f"the API rejected the request: {exc.message}" + _request_suffix(exc)
            ) from exc
        except sdk.RateLimitError as exc:
            raise LLMError("rate limited, even after retries; try again later") from exc
        except sdk.APIStatusError as exc:
            raise LLMError(
                f"the API answered with an error ({exc.status_code})" + _request_suffix(exc)
            ) from exc
        except sdk.APIConnectionError as exc:
            raise LLMError("could not reach the Anthropic API (network or timeout)") from exc
        except sdk.AnthropicError as exc:  # e.g. no credentials found at all
            raise LLMError(f"the Anthropic client failed: {exc}") from exc
        latency_ms = int(round((self._clock() - started) * 1000))
        usage = usage_from(message, latency_ms, request_id)

        if message.stop_reason == "refusal":
            category = getattr(message.stop_details, "category", None)
            raise LLMError(
                f"the model declined to answer ({category or 'no category given'})"
                + ("" if self.fallback else "; the refusal fallback was off"),
                usage=usage,
            )
        if message.stop_reason == "max_tokens":
            raise LLMError(
                f"the answer was cut off at {self.max_tokens} output tokens; "
                "raise --max-output-tokens or lower --effort",
                usage=usage,
            )
        text = "".join(block.text for block in message.content if block.type == "text")
        return Completion(text, usage)


def usage_from(message: Any, latency_ms: Optional[int], request_id: str = "") -> Usage:
    """Tokens and estimated cost of a message, across every model that worked on it.

    When a fallback ran, ``usage.iterations`` lists each attempt with its own model and the
    top-level usage covers only the last one, so the iterations are what gets counted.
    """
    usage = message.usage
    parts: List[Any] = list(usage.iterations or []) or [usage]
    out = Usage(
        model=message.model,
        latency_ms=latency_ms,
        stop_reason=message.stop_reason or "",
        request_id=request_id,
    )
    cost: Optional[float] = 0.0
    for part in parts:
        tokens = {
            "input_tokens": part.input_tokens or 0,
            "output_tokens": part.output_tokens or 0,
            "cache_read_tokens": part.cache_read_input_tokens or 0,
            "cache_write_tokens": part.cache_creation_input_tokens or 0,
        }
        for key, value in tokens.items():
            setattr(out, key, getattr(out, key) + value)
        part_cost = estimate_cost(getattr(part, "model", None) or message.model, **tokens)
        cost = None if cost is None or part_cost is None else cost + part_cost
    out.cost_usd = cost
    return out


def _request_suffix(exc: Any) -> str:
    request_id = getattr(exc, "request_id", None)
    return f" (request id {request_id})" if request_id else ""


def _import_sdk() -> Any:
    try:
        import anthropic
    except ImportError as exc:
        raise LLMError(INSTALL_HINT) from exc
    return anthropic
