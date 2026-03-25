"""Tests for PlannerAgent JSON parsing."""

from opentf.agents.specialists.planner import PlannerAgent

parse = PlannerAgent._parse_plan_json


def test_pure_json() -> None:
    result = parse('{"phases": []}')
    assert result is not None
    assert "phases" in result


def test_json_in_code_block() -> None:
    text = '```json\n{"phases": [{"name": "test", "steps": []}]}\n```'
    result = parse(text)
    assert result is not None
    assert result["phases"][0]["name"] == "test"


def test_json_with_text_before() -> None:
    text = 'Here is the plan:\n{"phases": []}'
    result = parse(text)
    assert result is not None


def test_json_with_text_around() -> None:
    text = 'Plan:\n{"phases": [{"name": "p1", "steps": []}]}\nDone.'
    result = parse(text)
    assert result is not None


def test_missing_phases_key() -> None:
    result = parse('{"label": "test", "steps": []}')
    assert result is None


def test_invalid_json() -> None:
    result = parse("this is not json at all")
    assert result is None


def test_empty_string() -> None:
    result = parse("")
    assert result is None


def test_json_with_nested_braces() -> None:
    text = '{"phases": [{"name": "p1", "steps": [{"id": "1.1", "name": "s", "description": "d"}]}]}'
    result = parse(text)
    assert result is not None
    assert len(result["phases"]) == 1
