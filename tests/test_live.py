"""One real call to the Anthropic API. Skipped unless you ask for it, because it costs money.

    TESTPLAN_LIVE=1 python -m pytest tests/test_live.py -s

Needs ``pip install -e ".[anthropic]"`` and credentials (``ANTHROPIC_API_KEY`` or
``ant auth login``). Plans the discount-cap demo change with the default model at low effort:
expect a few cents to a few tens of cents, printed at the end. CI never sets TESTPLAN_LIVE.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TESTPLAN_LIVE") != "1",
    reason="calls the real Anthropic API and costs money; set TESTPLAN_LIVE=1 to run",
)


def test_claude_drafts_a_well_formed_plan_for_the_demo_change(demo_bundles, demo):
    pytest.importorskip("anthropic")
    from testplan_agent.anthropic_client import AnthropicClient
    from testplan_agent.planner import generate_plan
    from testplan_agent.render import usage_summary

    result = generate_plan(
        demo_bundles["discount-cap"], AnthropicClient(effort="low"), repo=demo.repo
    )
    assert result.plan is not None, [str(i) for i in result.issues]
    total = result.plan.meta["run"]["total"]
    print(f"\n{result.attempts} attempt(s); {usage_summary(total)}")
    for issue in result.issues:
        print(issue)
    assert total["input_tokens"] > 0 and total["cost_usd"] is not None
