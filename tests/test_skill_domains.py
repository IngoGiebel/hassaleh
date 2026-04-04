"""Skill domain taxonomy tests for Sprint 8."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hassaleh.cli import (
    _build_domain_tree,
    _score_skill_match,
    build_parser,
    cmd_domain_list,
    cmd_skill_info,
    cmd_skill_list,
    cmd_skill_search,
)
from hassaleh.daemon import HassalehDaemon, _is_domain_allowed
from hassaleh.domain import (
    domain_hierarchy,
    domain_matches_any,
    domain_matches_prefix,
    format_domain_hierarchy,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent


class SyncResult:
    def __init__(self, records_data: list[dict]):
        self._records = records_data

    def single(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


class AsyncResultMock:
    def __init__(self, records_data: list[dict]):
        self._records = []
        for data in records_data:
            record = MagicMock()
            record.__getitem__ = lambda self, key, d=data: d[key]
            record.get = lambda key, default=None, d=data: d.get(key, default)
            self._records.append(record)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._records):
            raise StopAsyncIteration
        record = self._records[self._index]
        self._index += 1
        return record

    async def single(self):
        return self._records[0] if self._records else None


def make_sync_driver(session_mock=None):
    driver = MagicMock()
    session = session_mock or MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    ctx.__exit__.return_value = False
    driver.session.return_value = ctx
    driver.close = MagicMock()
    return driver, session


def make_async_driver(session_mock=None):
    driver = MagicMock()
    session = session_mock or AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    ctx.__aexit__.return_value = False
    driver.session.return_value = ctx
    return driver, session


def make_async_result(records_data: list[dict]) -> AsyncResultMock:
    return AsyncResultMock(records_data)


def test_skill_parser_list_command():
    parser = build_parser()
    args = parser.parse_args(["skill", "list"])
    assert args.command == "skill"
    assert args.skill_command == "list"


def test_skill_parser_search_command():
    parser = build_parser()
    args = parser.parse_args(["skill", "search", "metrics"])
    assert args.command == "skill"
    assert args.skill_command == "search"
    assert args.query == "metrics"


def test_skill_parser_info_command():
    parser = build_parser()
    args = parser.parse_args(["skill", "info", "exec-ls"])
    assert args.command == "skill"
    assert args.skill_command == "info"
    assert args.skill_id == "exec-ls"


def test_domain_parser_list_command():
    parser = build_parser()
    args = parser.parse_args(["domain", "list"])
    assert args.command == "domain"
    assert args.domain_command == "list"


def test_exact_match():
    assert domain_matches_prefix("orchestration.rules", "orchestration.rules") is True


def test_prefix_match():
    assert domain_matches_prefix("orchestration.rules", "orchestration") is True


def test_no_match():
    assert domain_matches_prefix("orchestration.rules", "security") is False


def test_prefix_match_does_not_cross_segment_boundaries():
    assert domain_matches_prefix("security.audit", "sec") is False


def test_domain_matches_any_allows_when_prefix_matches():
    assert domain_matches_any("reporting.metrics", ["reporting"]) is True


def test_domain_matches_any_denies_when_prefix_missing():
    assert domain_matches_any("reporting.metrics", ["analysis"]) is False


def test_domain_matches_any_empty_prefixes_allow_all():
    assert domain_matches_any("analysis.finance", []) is True


def test_domain_matches_any_unscoped_requires_opt_in():
    assert domain_matches_any(None, ["system"], allow_unscoped=True) is True


def test_domain_hierarchy_expands_all_levels():
    assert domain_hierarchy("system.health") == ["system", "system.health"]


def test_format_domain_hierarchy_human_readable():
    assert format_domain_hierarchy("system.health") == "system > health"


def test_score_skill_match_prefers_exact_name():
    exact = _score_skill_match("Graph Query Inspector", {"id": "a", "name": "Graph Query Inspector"})
    partial = _score_skill_match("Graph Query Inspector", {"id": "b", "name": "Graph Query"})
    assert exact < partial


def test_score_skill_match_prefers_name_over_description():
    name_hit = _score_skill_match("audit", {"id": "a", "name": "Audit Log Export", "description": ""})
    description_hit = _score_skill_match("audit", {"id": "b", "name": "Log Export", "description": "Audit export"})
    assert name_hit < description_hit


def test_build_domain_tree_aggregates_counts():
    lines = _build_domain_tree([
        {"id": "system", "parent": None, "exact_count": 0},
        {"id": "system.health", "parent": "system", "exact_count": 2},
        {"id": "system.config", "parent": "system", "exact_count": 1},
    ])
    assert any("system (3)" in line for line in lines)
    assert any("health (2)" in line for line in lines)


def test_skill_list(monkeypatch, capsys):
    driver, session = make_sync_driver()
    session.run.return_value = SyncResult([
        {"id": "exec-ls", "name": "List directory contents", "domain": "system.health", "kind": "cli", "lifecycle": "available"},
        {"id": "graph-query-inspector", "name": "Graph Query Inspector", "domain": "data.query", "kind": "skill", "lifecycle": "available"},
    ])
    monkeypatch.setattr("hassaleh.cli.get_connection", lambda args: {})
    monkeypatch.setattr("hassaleh.cli.connect", lambda conn: driver)

    args = build_parser().parse_args(["skill", "list"])
    rc = cmd_skill_list(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "exec-ls" in out
    assert "graph-query-inspector" in out


def test_skill_list_with_domain_filter(monkeypatch, capsys):
    driver, session = make_sync_driver()
    session.run.return_value = SyncResult([
        {"id": "exec-ls", "name": "List directory contents", "domain": "system.health", "kind": "cli", "lifecycle": "available"},
        {"id": "rule-author-basic", "name": "Rule Author", "domain": "orchestration.rules", "kind": "skill", "lifecycle": "available"},
    ])
    monkeypatch.setattr("hassaleh.cli.get_connection", lambda args: {})
    monkeypatch.setattr("hassaleh.cli.connect", lambda conn: driver)

    args = build_parser().parse_args(["skill", "list", "--domain", "orchestration"])
    rc = cmd_skill_list(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "rule-author-basic" in out
    assert "exec-ls" not in out


def test_skill_search(monkeypatch, capsys):
    driver, session = make_sync_driver()
    session.run.return_value = SyncResult([
        {"id": "metrics-daily-summary", "name": "Metrics Daily Summary", "domain": "reporting.metrics", "kind": "skill", "lifecycle": "available", "description": "Daily metrics summary"},
    ])
    monkeypatch.setattr("hassaleh.cli.get_connection", lambda args: {})
    monkeypatch.setattr("hassaleh.cli.connect", lambda conn: driver)

    args = build_parser().parse_args(["skill", "search", "metrics"])
    rc = cmd_skill_search(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "metrics-daily-summary" in out
    assert session.run.call_args.kwargs["query"] == "metrics"


def test_skill_search_sorts_exact_match_first(monkeypatch, capsys):
    driver, session = make_sync_driver()
    session.run.return_value = SyncResult([
        {"id": "audit-partial", "name": "Audit Trail", "domain": "security.audit", "kind": "skill", "lifecycle": "available", "description": "audit helpers"},
        {"id": "audit-log-export", "name": "Audit", "domain": "security.audit", "kind": "cli", "lifecycle": "available", "description": "export audit logs"},
    ])
    monkeypatch.setattr("hassaleh.cli.get_connection", lambda args: {})
    monkeypatch.setattr("hassaleh.cli.connect", lambda conn: driver)

    args = build_parser().parse_args(["skill", "search", "Audit"])
    cmd_skill_search(args)

    out = capsys.readouterr().out
    result_rows = [
        line for line in out.splitlines()
        if "audit-" in line.lower()
    ]
    first_row = result_rows[0]
    assert "audit-log-export" in first_row


def test_skill_info(monkeypatch, capsys):
    driver, session = make_sync_driver()
    session.run.side_effect = [
        SyncResult([{
            "capability": {
                "id": "exec-ls",
                "name": "List directory contents",
                "domain": "system.health",
                "kind": "cli",
                "lifecycle": "available",
            },
            "skill_domain_id": "system.health",
            "skill_domain_name": "System Health",
            "skill_domain_description": "Health checks and operational diagnostics.",
        }]),
        SyncResult([{
            "id": "dione",
            "name": "Dione",
            "lifecycle": "pending",
        }]),
    ]
    monkeypatch.setattr("hassaleh.cli.get_connection", lambda args: {})
    monkeypatch.setattr("hassaleh.cli.connect", lambda conn: driver)

    args = build_parser().parse_args(["skill", "info", "exec-ls"])
    rc = cmd_skill_info(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "system > health" in out
    assert "Dione" in out


def test_domain_list(monkeypatch, capsys):
    driver, session = make_sync_driver()
    session.run.return_value = SyncResult([
        {"id": "system", "name": "System", "parent": None, "exact_count": 0},
        {"id": "system.health", "name": "System Health", "parent": "system", "exact_count": 2},
        {"id": "reporting", "name": "Reporting", "parent": None, "exact_count": 0},
        {"id": "reporting.metrics", "name": "Metrics", "parent": "reporting", "exact_count": 1},
    ])
    monkeypatch.setattr("hassaleh.cli.get_connection", lambda args: {})
    monkeypatch.setattr("hassaleh.cli.connect", lambda conn: driver)

    args = build_parser().parse_args(["domain", "list"])
    rc = cmd_domain_list(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "system (2)" in out
    assert "health (2)" in out
    assert "reporting (1)" in out


@pytest.mark.asyncio
async def test_domain_permission_allowed():
    driver, session = make_async_driver()
    session.run = AsyncMock(return_value=make_async_result([{
        "allowed_domains": ["system"],
        "capability_domain": "system.health",
    }]))
    daemon = HassalehDaemon("bolt://x", "neo4j", "pw")
    daemon.driver = driver

    allowed, reason = await daemon._check_capability("dione", {"id": "intent-1"})
    assert allowed is True
    assert reason == ""


@pytest.mark.asyncio
async def test_domain_permission_denied():
    driver, session = make_async_driver()
    session.run = AsyncMock(return_value=make_async_result([{
        "allowed_domains": ["reporting"],
        "capability_domain": "system.health",
    }]))
    daemon = HassalehDaemon("bolt://x", "neo4j", "pw")
    daemon.driver = driver

    allowed, reason = await daemon._check_capability("dione", {"id": "intent-2"})
    assert allowed is False
    assert "not allowed" in reason


@pytest.mark.asyncio
async def test_domain_permission_null_allows_all():
    driver, session = make_async_driver()
    session.run = AsyncMock(return_value=make_async_result([{
        "allowed_domains": None,
        "capability_domain": "system.health",
    }]))
    daemon = HassalehDaemon("bolt://x", "neo4j", "pw")
    daemon.driver = driver

    allowed, _ = await daemon._check_capability("dione", {"id": "intent-3"})
    assert allowed is True


@pytest.mark.asyncio
async def test_domain_permission_unscoped_capability_allowed():
    driver, session = make_async_driver()
    session.run = AsyncMock(return_value=make_async_result([{
        "allowed_domains": ["system"],
        "capability_domain": None,
    }]))
    daemon = HassalehDaemon("bolt://x", "neo4j", "pw")
    daemon.driver = driver

    allowed, _ = await daemon._check_capability("dione", {"id": "intent-4"})
    assert allowed is True


@pytest.mark.asyncio
async def test_check_capability_missing_capability():
    driver, session = make_async_driver()
    session.run = AsyncMock(return_value=make_async_result([]))
    daemon = HassalehDaemon("bolt://x", "neo4j", "pw")
    daemon.driver = driver

    allowed, reason = await daemon._check_capability("dione", {"id": "intent-5"})
    assert allowed is False
    assert reason == "Agent lacks required capability"


def test_is_domain_allowed_matches_prefix():
    assert _is_domain_allowed(["integration"], "integration.openclaw") is True


def test_is_domain_allowed_rejects_outside_prefix():
    assert _is_domain_allowed(["integration.api"], "integration.openclaw") is False


def test_schema_has_capability_domain_index():
    schema = (PROJECT_DIR / "schema.cypher").read_text()
    assert "CREATE INDEX capability_domain IF NOT EXISTS" in schema


def test_schema_has_skilldomain_constraint():
    schema = (PROJECT_DIR / "schema.cypher").read_text()
    assert "CREATE CONSTRAINT skilldomain_id IF NOT EXISTS" in schema


def test_seed_exec_ls_has_domain():
    seed = (PROJECT_DIR / "seed.cypher").read_text()
    assert 'MERGE (cap:Capability {id: "exec-ls"})' in seed
    assert 'domain: "system.health"' in seed


def test_seed_contains_multiple_capability_domains():
    seed = (PROJECT_DIR / "seed.cypher").read_text()
    assert seed.count('domain: "') >= 7


def test_seed_contains_skilldomain_nodes():
    seed = (PROJECT_DIR / "seed.cypher").read_text()
    assert seed.count("MERGE (sd:SkillDomain") >= 40


def test_seed_contains_in_domain_relationships():
    seed = (PROJECT_DIR / "seed.cypher").read_text()
    assert seed.count("MERGE (cap)-[:IN_DOMAIN]->(sd);") >= 7


def test_capabilities_have_domains():
    seed = (PROJECT_DIR / "seed.cypher").read_text()
    capability_blocks = [block for block in seed.split("MERGE (cap:Capability") if "SET cap +=" in block]
    assert capability_blocks
    assert all('domain: "' in block for block in capability_blocks)


def test_docs_taxonomy_has_required_top_level_domains():
    doc = (PROJECT_DIR / "docs" / "DOMAIN_TAXONOMY.md").read_text()
    for domain in [
        "orchestration",
        "data",
        "security",
        "communication",
        "reporting",
        "system",
        "development",
        "integration",
        "analysis",
        "automation",
    ]:
        assert f"`{domain}`" in doc


def test_docs_taxonomy_has_at_least_30_subdomains():
    doc = (PROJECT_DIR / "docs" / "DOMAIN_TAXONOMY.md").read_text()
    subdomains = [
        line.strip()
        for line in doc.splitlines()
        if line.strip().startswith("- `") and "." in line
    ]
    assert len(subdomains) >= 30
