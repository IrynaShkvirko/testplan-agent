import pytest

from testplan_agent.pricing import PRICES, estimate_cost


def test_input_output_and_cache_are_priced_separately():
    # Claude Opus 5: $5 in, $25 out; cache writes 1.25x input, cache reads 0.1x input
    cost = estimate_cost("claude-opus-5", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert cost == pytest.approx(5 + 25 + 0.5 + 6.25)


def test_a_model_can_have_its_own_cache_read_price():
    assert estimate_cost("claude-fable-5-1", 0, 0, cache_read_tokens=1_000_000) == 0.25


def test_an_unknown_model_has_no_price_rather_than_a_zero_one():
    assert estimate_cost("claude-future-9", 10, 10) is None


def test_the_default_model_has_a_price():
    from testplan_agent.anthropic_client import DEFAULT_MODEL

    assert DEFAULT_MODEL in PRICES
