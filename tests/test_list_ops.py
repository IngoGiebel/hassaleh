"""Advanced list operation tests for Sprint 7."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from hassaleh.engine.compiler import compile_rule
from hassaleh.engine.parser import parse_rule
from hassaleh.engine.runtime import RuleContext


def assert_parses(rule_text: str) -> None:
    """Assert that a rule parses successfully."""
    tree = parse_rule(rule_text)
    assert tree is not None


def make_mock_context(
    match_rows: list[dict] | None = None,
    now: datetime | None = None,
) -> RuleContext:
    """Create a RuleContext with a mocked Neo4j session."""
    session = MagicMock()
    mock_records = list(match_rows or [])

    def _fake_execute_read(func):
        tx = MagicMock()
        tx.run.return_value = mock_records
        return func(tx)

    session.execute_read = _fake_execute_read
    session.run.return_value = mock_records

    return RuleContext(
        neo4j_session=session,
        rule_id="test-list-ops",
        priority=10,
        now=now or datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc),
    )


def exec_rule(source: str, ctx: RuleContext) -> None:
    """Execute compiled Python source against a context."""
    code = compile(source, "<gsl-ops>", "exec")
    ns: dict[str, object] = {}
    exec(code, ns)
    ns["evaluate"](ctx)


PARSER_FUNCTION_CASES = [
    'LET value = CONTAINS(["a", "b"], "a")',
    'LET value = APPEND([1, 2], 3)',
    'LET value = CONCAT([1, 2], [3, 4])',
    'LET value = FLATTEN([[1, 2], [3], 4])',
    'LET value = UNIQUE([1, 2, 1, 3, 2])',
    'LET value = SLICE([1, 2, 3, 4], 1, 3)',
    'LET value = SUM([1, 2, 3])',
    'LET value = AVG([2, 4, 6])',
    'LET value = FIRST([7, 8, 9])',
    'LET value = LAST([7, 8, 9])',
    'LET value = COUNT([7, 8, 9])',
    'LET value = ZIP([1, 2], ["a", "b"])',
    'LET value = ENUMERATE(["a", "b"])',
]


@pytest.mark.parametrize("stmt", PARSER_FUNCTION_CASES)
def test_parser_new_list_functions(stmt: str):
    assert_parses(f"""
MATCH (a:Agent):
    {stmt}
""")


def test_parser_nested_foreach():
    assert_parses("""
MATCH (a:Agent):
    FOREACH outer IN [1, 2]:
        FOREACH entry IN ["x", "y"]:
            LOG "pair"
""")


def test_parser_list_comp_simple():
    assert_parses("""
MATCH (a:Agent):
    LET values = [x FOR x IN [1, 2, 3]]
""")


def test_parser_list_comp_with_condition():
    assert_parses("""
MATCH (a:Agent):
    LET values = [x FOR x IN [1, 2, 3, 4] IF x > 2]
""")


COMPILER_CASES = [
    ("CONTAINS([1, 2], 2)", "ctx.contains([1, 2], 2)"),
    ("APPEND([1, 2], 3)", "ctx.append([1, 2], 3)"),
    ("CONCAT([1], [2, 3])", "ctx.concat([1], [2, 3])"),
    ("FLATTEN([[1], [2, 3]])", "ctx.flatten([[1], [2, 3]])"),
    ("UNIQUE([1, 2, 1])", "ctx.unique([1, 2, 1])"),
    ("SLICE([1, 2, 3, 4], 1, 3)", "ctx.slice([1, 2, 3, 4], 1, 3)"),
    ("SUM([1, 2, 3])", "sum([1, 2, 3])"),
    ("AVG([2, 4, 6])", "ctx.avg([2, 4, 6])"),
    ("FIRST([1, 2, 3])", "ctx.first([1, 2, 3])"),
    ("LAST([1, 2, 3])", "ctx.last([1, 2, 3])"),
    ("COUNT([1, 2, 3])", "len([1, 2, 3])"),
    ('ZIP([1, 2], ["a", "b"])', 'ctx.zip([1, 2], ["a", "b"])'),
    ('ENUMERATE(["a", "b"])', 'list(enumerate(["a", "b"]))'),
]


@pytest.mark.parametrize(("expr", "snippet"), COMPILER_CASES)
def test_compiler_new_list_functions(expr: str, snippet: str):
    source = compile_rule(f"""
MATCH (a:Agent):
    LET value = {expr}
""")
    assert snippet in source


def test_compiler_nested_foreach():
    source = compile_rule("""
MATCH (a:Agent):
    FOREACH outer IN [1, 2]:
        FOREACH entry IN ["x", "y"]:
            LOG "pair"
""")
    assert "for outer in [1, 2]:" in source
    assert 'for entry in ["x", "y"]:' in source


def test_compiler_list_comp_simple():
    source = compile_rule("""
MATCH (a:Agent):
    LET values = [x FOR x IN [1, 2, 3]]
""")
    assert "values = [x for x in [1, 2, 3]]" in source


def test_compiler_list_comp_with_condition():
    source = compile_rule("""
MATCH (a:Agent):
    LET values = [x FOR x IN [1, 2, 3, 4] IF x > 2]
""")
    assert "values = [x for x in [1, 2, 3, 4] if (x > 2)]" in source


EXEC_CASES = [
    ("CONTAINS([1, 2, 3], 2)", True),
    ("APPEND([1, 2], 3)", [1, 2, 3]),
    ("CONCAT([1, 2], [3, 4])", [1, 2, 3, 4]),
    ("FLATTEN([[1, 2], [3], 4])", [1, 2, 3, 4]),
    ("UNIQUE([1, 2, 1, 3, 2])", [1, 2, 3]),
    ("SLICE([1, 2, 3, 4], 1, 3)", [2, 3]),
    ("SUM([1, 2, 3])", 6),
    ("AVG([2, 4, 6])", 4),
    ("FIRST([7, 8, 9])", 7),
    ("LAST([7, 8, 9])", 9),
    ("COUNT([7, 8, 9])", 3),
    ('ZIP([1, 2], ["a", "b"])', [(1, "a"), (2, "b")]),
    ('ENUMERATE(["a", "b"])', [(0, "a"), (1, "b")]),
]


@pytest.mark.parametrize(("expr", "expected"), EXEC_CASES)
def test_e2e_list_function_values(expr: str, expected):
    source = compile_rule(f"""
MATCH (a:Agent):
    a.result = {expr}
""")
    agent = {"id": "agent-1"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert len(ctx.property_intents) == 1
    assert ctx.property_intents[0].value == expected


def test_e2e_flatten_none_graceful():
    source = compile_rule("""
MATCH (a:Agent):
    a.result = FLATTEN(null)
""")
    agent = {"id": "agent-1"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert ctx.property_intents[0].value == []


def test_e2e_avg_empty_returns_none():
    source = compile_rule("""
MATCH (a:Agent):
    a.result = AVG([])
""")
    agent = {"id": "agent-1"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert ctx.property_intents[0].value is None


def test_e2e_first_empty_returns_none():
    source = compile_rule("""
MATCH (a:Agent):
    a.result = FIRST([])
""")
    agent = {"id": "agent-1"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert ctx.property_intents[0].value is None


def test_e2e_last_empty_returns_none():
    source = compile_rule("""
MATCH (a:Agent):
    a.result = LAST([])
""")
    agent = {"id": "agent-1"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert ctx.property_intents[0].value is None


def test_e2e_nested_foreach_executes():
    source = compile_rule("""
MATCH (a:Agent):
    FOREACH outer IN [1, 2]:
        FOREACH entry IN ["x", "y"]:
            LOG "pair"
""")
    agent = {"id": "agent-1"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert len(ctx.logs) == 4


def test_e2e_list_comp_executes():
    source = compile_rule("""
MATCH (a:Agent):
    a.result = [x FOR x IN [1, 2, 3, 4] IF x > 2]
""")
    agent = {"id": "agent-1"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert ctx.property_intents[0].value == [3, 4]


def test_e2e_list_comp_with_property_access():
    source = compile_rule("""
MATCH (a:Agent):
    a.result = [item.value FOR item IN a.items IF item.enabled == true]
""")
    agent = {
        "id": "agent-1",
        "items": [
            {"value": 2, "enabled": True},
            {"value": 5, "enabled": False},
            {"value": 8, "enabled": True},
        ],
    }
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert ctx.property_intents[0].value == [2, 8]
