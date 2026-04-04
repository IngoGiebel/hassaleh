"""CLI tests for Hassaleh — Sprint 3.

Tests CLI output formatting and command structure.
Run with: PYTHONPATH=src pytest tests/test_cli.py -v
"""

from __future__ import annotations

import pytest

from hassaleh.cli_fmt import (
    fmt_table, fmt_kv, fmt_ok, fmt_warn, fmt_error, fmt_code,
)
from hassaleh.cli import build_parser


# ══════════════════════════════════════════════
# 1. Formatter tests
# ══════════════════════════════════════════════

def test_fmt_table_basic():
    """Basic table formatting."""
    result = fmt_table(
        ["Name", "Age"],
        [["Alice", "30"], ["Bob", "25"]],
        indent=0,
    )
    assert "Name" in result
    assert "Alice" in result
    assert "Bob" in result


def test_fmt_table_empty():
    """Empty table shows (empty)."""
    result = fmt_table(["A", "B"], [], indent=0)
    assert "(empty)" in result


def test_fmt_kv():
    """Key-value formatting."""
    result = fmt_kv("Status", "healthy", indent=0)
    assert "Status" in result
    assert "healthy" in result


def test_fmt_ok():
    assert "✅" in fmt_ok("good")

def test_fmt_warn():
    assert "⚠️" in fmt_warn("warning")

def test_fmt_error():
    assert "❌" in fmt_error("bad")

def test_fmt_code():
    """Code block with line numbers."""
    result = fmt_code("line1\nline2\nline3")
    assert "1" in result
    assert "line1" in result
    assert "3" in result


# ══════════════════════════════════════════════
# 2. Parser tests
# ══════════════════════════════════════════════

def test_parser_status():
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status"


def test_parser_init():
    parser = build_parser()
    args = parser.parse_args(["init"])
    assert args.command == "init"


def test_parser_agent_list():
    parser = build_parser()
    args = parser.parse_args(["agent", "list"])
    assert args.command == "agent"
    assert args.agent_command == "list"


def test_parser_agent_info():
    parser = build_parser()
    args = parser.parse_args(["agent", "info", "dione"])
    assert args.command == "agent"
    assert args.agent_command == "info"
    assert args.agent_id == "dione"


def test_parser_rule_list():
    parser = build_parser()
    args = parser.parse_args(["rule", "list"])
    assert args.command == "rule"
    assert args.rule_command == "list"


def test_parser_rule_compile():
    parser = build_parser()
    args = parser.parse_args(["rule", "compile", "agent-health-check", "--dry-run"])
    assert args.command == "rule"
    assert args.rule_command == "compile"
    assert args.rule_id == "agent-health-check"
    assert args.dry_run is True


def test_parser_intent_list():
    parser = build_parser()
    args = parser.parse_args(["intent", "list", "--lifecycle", "pending", "--limit", "5"])
    assert args.command == "intent"
    assert args.intent_command == "list"
    assert args.lifecycle == "pending"
    assert args.limit == 5


def test_parser_approve():
    parser = build_parser()
    args = parser.parse_args(["approve", "abc-123"])
    assert args.command == "approve"
    assert args.intent_id == "abc-123"


def test_parser_global_flags():
    parser = build_parser()
    args = parser.parse_args(["--uri", "bolt://remote:7687", "--user", "admin", "status"])
    assert args.uri == "bolt://remote:7687"
    assert args.user == "admin"
    assert args.command == "status"


def test_parser_report_daily():
    parser = build_parser()
    args = parser.parse_args(["report", "daily", "--format", "json", "--days", "7"])
    assert args.command == "report"
    assert args.report_command == "daily"
    assert args.format == "json"
    assert args.days == 7


def test_parser_report_agents():
    parser = build_parser()
    args = parser.parse_args(["report", "agents", "--format", "markdown"])
    assert args.report_command == "agents"
    assert args.format == "markdown"


def test_parser_report_intents():
    parser = build_parser()
    args = parser.parse_args(["report", "intents"])
    assert args.report_command == "intents"
    assert args.format == "text"  # default


def test_parser_skill_list():
    parser = build_parser()
    args = parser.parse_args(["skill", "list", "--domain", "orchestration"])
    assert args.command == "skill"
    assert args.skill_command == "list"
    assert args.domain == "orchestration"


def test_parser_skill_search():
    parser = build_parser()
    args = parser.parse_args(["skill", "search", "graph"])
    assert args.command == "skill"
    assert args.skill_command == "search"
    assert args.query == "graph"


def test_parser_skill_info():
    parser = build_parser()
    args = parser.parse_args(["skill", "info", "exec-ls"])
    assert args.command == "skill"
    assert args.skill_command == "info"
    assert args.skill_id == "exec-ls"


def test_parser_domain_list():
    parser = build_parser()
    args = parser.parse_args(["domain", "list"])
    assert args.command == "domain"
    assert args.domain_command == "list"


def test_parser_no_command():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.command is None
