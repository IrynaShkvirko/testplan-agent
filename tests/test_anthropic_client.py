"""The Anthropic client, driven by a fake SDK client: no network, no credentials, no cost.

Messages and errors are real SDK objects, so the tests break if the SDK changes shape.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

from anthropic.types.beta import BetaMessage  # noqa: E402

from testplan_agent.anthropic_client import (  # noqa: E402
    DEFAULT_MODEL,
    FALLBACK_BETA,
    AnthropicClient,
    usage_from,
)
from testplan_agent.llm import LLMError  # noqa: E402
from testplan_agent.planner import generate_plan  # noqa: E402
from testplan_agent.schema import model_schema  # noqa: E402

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def message(
    text="{}",
    stop_reason="end_turn",
    model=DEFAULT_MODEL,
    usage=None,
    stop_details=None,
):
    content = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if text is not None:
        content.append({"type": "text", "text": text})
    return BetaMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "stop_details": stop_details,
            "usage": usage
            or {
                "input_tokens": 1000,
                "output_tokens": 4000,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 9000,
            },
        }
    )


def status_error(cls, status, text="nope"):
    response = httpx2.Response(status, request=_REQUEST, headers={"request-id": "req_err"})
    return cls(text, response=response, body=None)


class _Stream(SimpleNamespace):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSDK:
    """Stands in for ``anthropic.Anthropic()``: each call plays the next outcome."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        self.beta = SimpleNamespace(messages=self)

    def stream(self, **params):
        self.requests.append(copy.deepcopy(params))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _Stream(get_final_message=lambda: outcome, request_id=f"req_{len(self.requests)}")


def ticking_clock(step=2.5):
    """Each reading is ``step`` seconds after the last, so every call takes ``step`` seconds."""
    now = [0.0]

    def clock():
        now[0] += step
        return now[0]

    return clock


def client(*outcomes, **kwargs):
    sdk = FakeSDK(*outcomes)
    return AnthropicClient(sdk_client=sdk, clock=ticking_clock(), **kwargs), sdk


# ---- the request ----------------------------------------------------------------------------
def test_the_request_asks_for_the_plan_schema_with_thinking_caching_and_fallback():
    c, _ = client()
    params = c.request("SYSTEM", [{"role": "user", "content": "facts"}])
    assert params["model"] == "claude-opus-5"
    assert params["system"] == "SYSTEM"
    assert params["messages"] == [{"role": "user", "content": "facts"}]
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"]["effort"] == "high"
    fmt = params["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] == anthropic.transform_schema(model_schema())
    assert params["cache_control"] == {"type": "ephemeral"}
    assert params["betas"] == [FALLBACK_BETA] and params["fallbacks"] == "default"


def test_limits_the_api_cannot_enforce_are_described_not_dropped():
    c, _ = client()
    schema = c.request("S", [{"role": "user", "content": "x"}])["output_config"]["format"]
    likelihood = schema["schema"]["properties"]["risks"]["items"]["properties"]["likelihood"]
    assert "minimum" not in likelihood and "minimum: 1" in likelihood["description"]


def test_options_change_the_request():
    c, _ = client(model="claude-sonnet-5", effort="low", max_tokens=8000, fallback=False)
    params = c.request("S", [{"role": "user", "content": "x"}])
    assert (params["model"], params["max_tokens"]) == ("claude-sonnet-5", 8000)
    assert params["output_config"]["effort"] == "low"
    assert "betas" not in params and "fallbacks" not in params


def test_an_unknown_effort_is_refused():
    with pytest.raises(ValueError):
        AnthropicClient(sdk_client=FakeSDK(), effort="extreme")


# ---- answers and usage ----------------------------------------------------------------------
def test_a_plan_from_claude_goes_through_the_normal_checks(discount):
    c, sdk = client(message(json.dumps(discount.plan)))
    result = generate_plan(discount.bundle, c, repo=discount.repo)
    assert result.ok and result.attempts == 1
    run = result.plan.meta["run"]
    assert result.plan.meta["generator"] == "anthropic"
    (attempt,) = run["attempts"]
    assert attempt["model"] == "claude-opus-5" and attempt["request_id"] == "req_1"
    assert attempt["latency_ms"] == 2500 and attempt["stop_reason"] == "end_turn"
    # 1000 in at $5, 4000 out at $25, 9000 cache writes at $6.25 per million
    assert attempt["cost_usd"] == pytest.approx(0.005 + 0.1 + 0.05625)
    assert "<untrusted-context>" in sdk.requests[0]["messages"][0]["content"]


def test_a_repair_resends_the_same_context_and_reads_it_from_cache(discount):
    bad = copy.deepcopy(discount.plan)
    bad["cases"][0]["evidence"].append("F999")
    cached = {
        "input_tokens": 500,
        "output_tokens": 4000,
        "cache_read_input_tokens": 9000,
        "cache_creation_input_tokens": 0,
    }
    c, sdk = client(message(json.dumps(bad)), message(json.dumps(discount.plan), usage=cached))
    result = generate_plan(discount.bundle, c, repo=discount.repo)
    assert result.ok and result.attempts == 2
    first, second = (r["messages"] for r in sdk.requests)
    assert second[0] == first[0] and [m["role"] for m in second] == ["user", "assistant", "user"]
    assert result.plan.meta["run"]["total"]["cache_read_tokens"] == 9000


def test_a_fallback_is_counted_at_each_models_price():
    usage = {
        "input_tokens": 1000,
        "output_tokens": 2000,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "iterations": [
            {"type": "message", "model": "claude-opus-5", "input_tokens": 1000,
             "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            {"type": "fallback_message", "model": "claude-sonnet-5", "input_tokens": 1000,
             "output_tokens": 2000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        ],
    }  # fmt: skip
    u = usage_from(message(model="claude-sonnet-5", usage=usage), 100)
    assert u.model == "claude-sonnet-5"  # who answered
    assert (u.input_tokens, u.output_tokens) == (2000, 2000)
    assert u.cost_usd == pytest.approx(1000 * 5e-6 + 1000 * 2e-6 + 2000 * 10e-6)


def test_a_model_with_no_known_price_has_an_unknown_cost():
    assert usage_from(message(model="claude-future-9"), 10).cost_usd is None


# ---- failures -------------------------------------------------------------------------------
def test_a_refusal_is_an_error_that_still_reports_its_cost(discount):
    refused = message(
        text=None,
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": None},
        usage={"input_tokens": 1000, "output_tokens": 50},
    )
    c, _ = client(refused)
    with pytest.raises(LLMError, match="declined to answer \\(cyber\\)") as info:
        generate_plan(discount.bundle, c, repo=discount.repo)
    assert info.value.spent["input_tokens"] == 1000 and info.value.spent["cost_usd"] > 0


def test_a_refusal_during_repair_keeps_the_earlier_plan(discount):
    bad = copy.deepcopy(discount.plan)
    bad["cases"][0]["evidence"].append("F999")
    c, _ = client(message(json.dumps(bad)), message(text=None, stop_reason="refusal"))
    result = generate_plan(discount.bundle, c, repo=discount.repo)
    assert result.plan is not None and not result.ok
    codes = {i["code"] for i in result.plan.validation}
    assert {"unknown_fact", "client_error"} <= codes
    assert len(result.plan.meta["run"]["attempts"]) == 2


def test_an_answer_cut_off_at_the_token_limit_says_how_to_fix_it():
    c, _ = client(message(text='{"cases": [', stop_reason="max_tokens"), max_tokens=8000)
    with pytest.raises(LLMError, match="cut off at 8000 output tokens.*--max-output-tokens"):
        c.complete("S", [{"role": "user", "content": "x"}])


@pytest.mark.parametrize(
    "error,expected",
    [
        (status_error(anthropic.AuthenticationError, 401), "ANTHROPIC_API_KEY or run `ant auth"),
        (status_error(anthropic.NotFoundError, 404), "unknown model 'claude-opus-5'"),
        (status_error(anthropic.BadRequestError, 400, "bad schema"), "rejected the request"),
        (status_error(anthropic.RateLimitError, 429), "rate limited"),
        (status_error(anthropic.InternalServerError, 500), "error \\(500\\).*req_err"),
        (anthropic.APIConnectionError(request=_REQUEST), "could not reach"),
    ],
)
def test_api_errors_become_short_messages(error, expected):
    c, _ = client(error)
    with pytest.raises(LLMError, match=expected):
        c.complete("S", [{"role": "user", "content": "x"}])


# ---- the command line -----------------------------------------------------------------------
def test_generate_with_claude_writes_the_plan_and_prints_usage(
    discount, demo, tmp_path, capsys, monkeypatch
):
    from conftest import AS_OF

    from testplan_agent.cli import main

    sdk = FakeSDK(message(json.dumps(discount.plan)))
    monkeypatch.setattr(anthropic, "Anthropic", lambda: sdk)
    name = "discount-cap"
    argv = [
        "generate",
        "--diff", str(demo.changes / name / "change.patch"),
        "--spec", str(demo.changes / name / "story.md"),
        "--repo", str(demo.repo),
        "--as-of", AS_OF,
        "--client", "anthropic",
        "--effort", "medium",
        "-o", str(tmp_path / "plan.md"),
    ]  # fmt: skip
    assert main(argv) == 0
    assert sdk.requests[0]["output_config"]["effort"] == "medium"
    assert "model usage: 1,000 tokens in, 4,000 out" in capsys.readouterr().err
    assert "| 1 | claude-opus-5 |" in (tmp_path / "plan.md").read_text()
