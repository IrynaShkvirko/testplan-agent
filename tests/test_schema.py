import json
from pathlib import Path

import pytest

from testplan_agent.cli import main
from testplan_agent.schema import PLAN_SCHEMA, TestPlan, schema_errors

ROOT = Path(__file__).resolve().parent.parent


def test_the_committed_schema_file_matches_the_code(capsys):
    assert main(["schema"]) == 0
    printed = capsys.readouterr().out
    assert (ROOT / "schema" / "testplan.schema.json").read_text() == printed


def test_the_schema_is_valid_json_and_names_its_draft():
    doc = json.loads((ROOT / "schema" / "testplan.schema.json").read_text())
    assert doc["$schema"].endswith("2020-12/schema")
    assert doc["type"] == "object" and "cases" in doc["properties"]


def test_a_generated_plan_satisfies_the_schema(discount):
    assert schema_errors(discount.plan, PLAN_SCHEMA) == []


def test_plan_round_trips_through_the_dataclasses(discount):
    plan = TestPlan.from_dict(discount.plan)
    assert plan.to_dict() == discount.plan


@pytest.mark.parametrize(
    "instance,schema,fragment",
    [
        ("x", {"type": "integer"}, "expected integer"),
        (True, {"type": "integer"}, "expected integer"),  # a bool is not a number here
        ("c", {"enum": ["a", "b"]}, "not one of"),
        ("", {"type": "string", "minLength": 1}, "must not be empty"),
        ("abc", {"type": "string", "pattern": "^F[0-9]+$"}, "does not match"),
        (0, {"type": "integer", "minimum": 1}, "below 1"),
        (9, {"type": "integer", "maximum": 5}, "above 5"),
        ([], {"type": "array", "minItems": 1}, "at least 1"),
        ([1, "x"], {"type": "array", "items": {"type": "integer"}}, "$[1]: expected integer"),
        ({}, {"type": "object", "required": ["a"]}, "missing required field 'a'"),
        (
            {"b": 1},
            {"type": "object", "properties": {}, "additionalProperties": False},
            "unexpected field 'b'",
        ),
    ],
)
def test_the_mini_validator_reports_each_kind_of_problem(instance, schema, fragment):
    errors = schema_errors(instance, schema)
    assert any(fragment in e for e in errors), errors


def test_a_valid_instance_has_no_errors():
    schema = {
        "type": "object",
        "required": ["a"],
        "additionalProperties": False,
        "properties": {"a": {"type": "array", "items": {"type": "string", "minLength": 1}}},
    }
    assert schema_errors({"a": ["x"]}, schema) == []
