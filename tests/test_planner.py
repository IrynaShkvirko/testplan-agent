"""The generate-validate-repair loop, driven by scripted models so no network is involved."""

from __future__ import annotations

import copy
import json

import pytest

from testplan_agent.llm import HeuristicClient, ScriptedClient
from testplan_agent.planner import generate_plan, parse_model_json


def bad_plan(discount):
    """Well-formed JSON with one grounding error: an invented fact id."""
    plan = copy.deepcopy(discount.plan)
    plan["cases"][0]["evidence"].append("F999")
    return plan


def test_a_good_first_answer_is_accepted_without_repair(discount):
    result = generate_plan(discount.bundle, HeuristicClient(), repo=discount.repo)
    assert result.ok and result.attempts == 1 and result.issues == []
    assert result.plan.meta["generator"] == "heuristic-baseline"
    assert len(result.plan.meta["bundle_sha256"]) == 64


def test_repair_succeeds_when_the_second_answer_fixes_the_problem(discount):
    client = ScriptedClient([bad_plan(discount), discount.plan])
    result = generate_plan(discount.bundle, client, repo=discount.repo)
    assert result.ok and result.attempts == 2
    repair_request = client.calls[1]["user"]
    assert "previous answer had these problems" in repair_request
    assert "F999" in repair_request  # the model is told exactly what was wrong


def test_three_bad_answers_ship_the_plan_with_its_errors_attached(discount):
    client = ScriptedClient([bad_plan(discount)])  # the last reply repeats
    result = generate_plan(discount.bundle, client, repo=discount.repo, max_repairs=2)
    assert not result.ok and result.attempts == 3 and len(client.calls) == 3
    assert result.plan is not None
    assert any(i["code"] == "unknown_fact" for i in result.plan.validation)


def test_no_repairs_means_one_attempt(discount):
    client = ScriptedClient([bad_plan(discount)])
    result = generate_plan(discount.bundle, client, repo=discount.repo, max_repairs=0)
    assert result.attempts == 1 and len(client.calls) == 1


def test_an_answer_that_is_not_json_is_repaired_too(discount):
    client = ScriptedClient(["Sure! Here is your plan.", discount.plan])
    result = generate_plan(discount.bundle, client, repo=discount.repo)
    assert result.ok and result.attempts == 2
    assert "not valid JSON" in client.calls[1]["user"]


def test_json_in_a_code_fence_or_with_chatter_is_read(discount):
    fenced = "```json\n" + json.dumps(discount.plan) + "\n```"
    chatty = "Here you go:\n" + json.dumps(discount.plan) + "\nHope that helps!"
    for reply in (fenced, chatty):
        result = generate_plan(discount.bundle, ScriptedClient([reply]), repo=discount.repo)
        assert result.ok and result.attempts == 1


def test_an_earlier_plan_is_kept_when_a_later_answer_is_not_json(discount):
    client = ScriptedClient([bad_plan(discount), "I am unable to continue."])
    result = generate_plan(discount.bundle, client, repo=discount.repo, max_repairs=1)
    assert not result.ok
    assert result.plan is not None  # the earlier, well-formed plan is not thrown away
    assert any(i["code"] == "unknown_fact" for i in result.plan.validation)


def test_no_plan_at_all_when_nothing_was_well_formed(discount):
    client = ScriptedClient(["nope"])
    result = generate_plan(discount.bundle, client, repo=discount.repo)
    assert result.plan is None and not result.ok
    assert any(i.code == "json" for i in result.issues)


def test_wrong_shape_is_reported_as_schema_errors(discount):
    client = ScriptedClient([{"cases": "x"}])
    result = generate_plan(discount.bundle, client, repo=discount.repo, max_repairs=0)
    assert result.plan is None
    assert any(i.code == "schema" for i in result.issues)


def test_a_callable_reply_sees_the_prompts(discount):
    seen = {}

    def reply(system, user):
        seen["system"], seen["user"] = system, user
        return json.dumps(discount.plan)

    generate_plan(discount.bundle, ScriptedClient([reply]), repo=discount.repo)
    assert "JSON Schema" in seen["system"] and "<untrusted-context>" in seen["user"]


def test_scripted_client_needs_a_reply():
    with pytest.raises(ValueError):
        ScriptedClient([])


@pytest.mark.parametrize("text", ["{}", ' {"a": 1} ', '```\n{"a": 1}\n```', 'x {"a": 1} y'])
def test_parse_model_json_accepts_reasonable_wrapping(text):
    assert isinstance(parse_model_json(text), dict)


def test_parse_model_json_rejects_prose():
    with pytest.raises(ValueError):
        parse_model_json("no json here")


def test_a_deterministic_client_is_not_asked_to_repair(discount):
    class Fixed(ScriptedClient):
        deterministic = True

    client = Fixed([bad_plan(discount)])
    result = generate_plan(discount.bundle, client, repo=discount.repo, max_repairs=2)
    assert result.attempts == 1 and len(client.calls) == 1 and not result.ok


def test_the_plan_with_the_fewest_errors_is_kept(discount):
    worse = bad_plan(discount)
    worse["cases"][1]["evidence"].append("F998")
    client = ScriptedClient([bad_plan(discount), worse])
    result = generate_plan(discount.bundle, client, repo=discount.repo, max_repairs=1)
    errors = [i for i in result.plan.validation if i["severity"] == "error"]
    assert len(errors) == 1 and "F999" in errors[0]["message"]
