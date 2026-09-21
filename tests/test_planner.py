"""The generate-validate-repair loop, driven by scripted models so no network is involved."""

from __future__ import annotations

import copy
import json

import pytest

from testplan_agent.llm import HeuristicClient, ScriptedClient, Usage
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
    first, second = client.calls[0]["turns"], client.calls[1]["turns"]
    assert [t["role"] for t in second] == ["user", "assistant", "user"]
    assert second[0] == first[0]  # the context is sent unchanged, so it can be read from cache
    assert "previous answer had these problems" in second[-1]["content"]
    assert "F999" in second[-1]["content"]  # the model is told exactly what was wrong


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
    assert "not valid JSON" in client.calls[1]["turns"][-1]["content"]


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

    def reply(system, turns):
        seen["system"], seen["turns"] = system, turns
        return json.dumps(discount.plan)

    generate_plan(discount.bundle, ScriptedClient([reply]), repo=discount.repo)
    assert "JSON Schema" in seen["system"]
    assert "<untrusted-context>" in seen["turns"][0]["content"]


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


def test_repair_turns_are_appended_never_rewritten(discount):
    client = ScriptedClient([bad_plan(discount)])
    generate_plan(discount.bundle, client, repo=discount.repo, max_repairs=2)
    calls = [c["turns"] for c in client.calls]
    assert [len(t) for t in calls] == [1, 3, 5]
    assert calls[2][:3] == calls[1]  # each request extends the last one


def test_meta_is_written_by_the_tool_not_taken_from_the_answer(discount):
    answer = copy.deepcopy(discount.plan)
    answer["meta"] = {"generator": "me", "created": "yesterday", "bundle_sha256": "0" * 64}
    answer["validation"] = [{"code": "x", "severity": "warning", "where": "", "message": "fake"}]
    result = generate_plan(discount.bundle, ScriptedClient([answer]), repo=discount.repo)
    assert result.ok and result.plan.meta["generator"] == "scripted"
    assert result.plan.meta["bundle_sha256"] != "0" * 64 and result.plan.validation == []


def test_an_answer_with_no_meta_at_all_is_accepted(discount):
    answer = {k: v for k, v in discount.plan.items() if k not in ("meta", "validation")}
    assert generate_plan(discount.bundle, ScriptedClient([answer]), repo=discount.repo).ok


def test_the_rule_based_baseline_records_no_model_time_and_no_cost(discount):
    result = generate_plan(discount.bundle, HeuristicClient(), repo=discount.repo)
    run = result.plan.meta["run"]
    assert len(run["attempts"]) == 1
    assert run["total"]["cost_usd"] == 0.0 and run["total"]["latency_ms"] is None
    assert run["total"]["input_tokens"] == 0


class _Metered(ScriptedClient):
    """A scripted client whose calls report usage, as a real model client would."""

    def __init__(self, replies, costs):
        super().__init__(replies)
        self.costs = list(costs)

    def complete(self, system, turns):
        completion = super().complete(system, turns)
        n = len(self.calls) - 1
        completion.usage = Usage(
            model="m",
            input_tokens=1000,
            output_tokens=200,
            cache_read_tokens=900 if n else 0,
            cache_write_tokens=0 if n else 900,
            latency_ms=1500,
            cost_usd=self.costs[n],
            stop_reason="end_turn",
            request_id=f"req_{n}",
        )
        return completion


def test_usage_is_recorded_per_attempt_and_totalled(discount):
    client = _Metered([bad_plan(discount), discount.plan], costs=[0.25, 0.125])
    result = generate_plan(discount.bundle, client, repo=discount.repo)
    run = result.plan.meta["run"]
    assert [a["request_id"] for a in run["attempts"]] == ["req_0", "req_1"]
    assert run["total"] == {
        "input_tokens": 2000,
        "output_tokens": 400,
        "cache_read_tokens": 900,
        "cache_write_tokens": 900,
        "latency_ms": 3000,
        "cost_usd": 0.375,
    }


def test_a_total_is_unknown_when_any_part_of_it_is(discount):
    client = _Metered([bad_plan(discount), discount.plan], costs=[0.25, None])
    run = generate_plan(discount.bundle, client, repo=discount.repo).plan.meta["run"]
    assert run["total"]["cost_usd"] is None and run["total"]["input_tokens"] == 2000
