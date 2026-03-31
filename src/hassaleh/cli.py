#!/usr/bin/env python3.13
"""Hassaleh CLI — Command-line interface for operators and admins.

Usage:
    hassaleh status          Show Daemon health, agents, rules, intents
    hassaleh init            Initialize Neo4j schema + seed data
    hassaleh agent list      List all agents
    hassaleh agent info ID   Show agent details
    hassaleh rule list       List all rules
    hassaleh rule compile ID Force-recompile a rule, show generated Python
    hassaleh intent list     List recent intents
    hassaleh approve ID      Approve an awaiting_approval intent

Reference: docs/CONCEPT.md v1.2
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Suppress Neo4j "property does not exist" warnings (noisy but harmless)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

from neo4j import GraphDatabase

from hassaleh.cli_fmt import (
    fmt_table, fmt_header, fmt_ok, fmt_warn, fmt_error,
    fmt_kv, fmt_section, fmt_code,
)


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

DEFAULT_URI = "bolt://localhost:7690"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = ""
HEALTH_URL = "http://127.0.0.1:9100/health"
PROJECT_DIR = Path(__file__).parent.parent.parent  # hassaleh project root


def get_connection(args) -> dict:
    """Resolve Neo4j connection params from args → env → config → defaults."""
    uri = args.uri or os.environ.get("NEO4J_URI", DEFAULT_URI)
    user = args.user or os.environ.get("NEO4J_USER", DEFAULT_USER)
    password = args.password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)

    # Try config file
    if not password:
        config_path = Path.home() / ".hassaleh" / "config.toml"
        if config_path.exists():
            try:
                import tomllib
                with open(config_path, "rb") as f:
                    cfg = tomllib.load(f)
                uri = uri or cfg.get("neo4j", {}).get("uri", DEFAULT_URI)
                user = user or cfg.get("neo4j", {}).get("user", DEFAULT_USER)
                password = password or cfg.get("neo4j", {}).get("password", "")
            except Exception:
                pass

    return {"uri": uri, "user": user, "password": password}


def connect(conn: dict):
    """Create a Neo4j driver and verify connectivity."""
    driver = GraphDatabase.driver(conn["uri"], auth=(conn["user"], conn["password"]))
    driver.verify_connectivity()
    return driver


# ──────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────

def cmd_status(args) -> int:
    """Show Daemon health, agents, rules, pending intents."""
    # 1. Daemon health (via HTTP)
    fmt_header("Hassaleh Status")

    try:
        import urllib.request
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
            health = json.loads(resp.read())
        print(fmt_section("Daemon"))
        print(fmt_kv("Status", fmt_ok(health["status"])))
        print(fmt_kv("Uptime", f"{health['uptime_seconds']}s"))
        print(fmt_kv("Ticks", str(health["tick_count"])))
        print(fmt_kv("Active Workers", str(health["active_workers"])))
        print(fmt_kv("Schema", health.get("schema_version", "?")))
        print(fmt_kv("Rules loaded", str(health.get("rules_loaded", "?"))))
        print(fmt_kv("OpenClaw Bridge", health.get("openclaw_bridge", "?")))
        print(fmt_kv("Notifications", health.get("notifications", "?")))
    except Exception as e:
        print(fmt_section("Daemon"))
        print(fmt_kv("Status", fmt_error("UNREACHABLE")))
        print(fmt_kv("Error", str(e)))

    # 2. Neo4j connection
    conn = get_connection(args)
    try:
        driver = connect(conn)
    except Exception as e:
        print(fmt_section("Neo4j"))
        print(fmt_kv("Status", fmt_error(f"Connection failed: {e}")))
        return 1

    with driver.session() as session:
        # 3. Agents
        result = session.run("""
            MATCH (a:Agent)
            RETURN a.id AS id, a.name AS name, a.lifecycle AS lifecycle,
                   a.last_heartbeat AS hb
            ORDER BY a.name
        """)
        agents = [dict(r) for r in result]

        print(fmt_section("Agents"))
        if agents:
            rows = [[a["id"], a.get("name", ""), a.get("lifecycle", "?"),
                      str(a.get("hb", "—"))] for a in agents]
            print(fmt_table(["ID", "Name", "Lifecycle", "Last Heartbeat"], rows))
        else:
            print("  No agents found")

        # 4. Rules
        result = session.run("""
            MATCH (r:Rule)
            RETURN r.id AS id, r.name AS name, r.lifecycle AS lifecycle,
                   r.priority AS priority, r.compiler_version AS cv
            ORDER BY r.priority ASC
        """)
        rules = [dict(r) for r in result]

        print(fmt_section("Rules"))
        if rules:
            rows = [[r["id"], r.get("name", ""), r.get("lifecycle", "?"),
                      str(r.get("priority", "?")), r.get("cv", "—")] for r in rules]
            print(fmt_table(["ID", "Name", "Lifecycle", "Priority", "Compiler"], rows))
        else:
            print("  No rules found")

        # 5. Pending Intents
        result = session.run("""
            MATCH (i:Intent)
            WHERE i.lifecycle IN ['pending', 'claimed', 'running', 'awaiting_approval']
            RETURN i.id AS id, i.lifecycle AS lifecycle, i.action AS action,
                   i.submitted_at AS submitted, i.source AS source
            ORDER BY i.submitted_at DESC
            LIMIT 10
        """)
        intents = [dict(r) for r in result]

        print(fmt_section("Active Intents"))
        if intents:
            rows = [[i["id"][:12] + "…", i.get("lifecycle", "?"),
                      i.get("action", "?"), str(i.get("submitted", "?")),
                      i.get("source", "agent")] for i in intents]
            print(fmt_table(["ID", "Lifecycle", "Action", "Submitted", "Source"], rows))
        else:
            print("  No active intents")

    driver.close()
    return 0


def cmd_init(args) -> int:
    """Initialize Neo4j schema + seed data."""
    fmt_header("Hassaleh Init")
    conn = get_connection(args)

    try:
        driver = connect(conn)
    except Exception as e:
        print(fmt_error(f"Connection failed: {e}"))
        return 1

    files = [
        ("Schema", PROJECT_DIR / "schema.cypher"),
        ("Seed Data", PROJECT_DIR / "seed.cypher"),
        ("Seed Rules", PROJECT_DIR / "seed_rules.cypher"),
    ]

    errors = 0
    with driver.session() as session:
        for label, path in files:
            if not path.exists():
                print(fmt_warn(f"{label}: {path} not found, skipping"))
                continue

            cypher = path.read_text()
            # Split on semicolons and execute each statement
            statements = [s.strip() for s in cypher.split(";") if s.strip()]
            file_errors = 0
            for stmt in statements:
                # Skip comments-only blocks
                lines = [l for l in stmt.split("\n") if l.strip() and not l.strip().startswith("//")]
                if not lines:
                    continue
                try:
                    session.run(stmt)
                except Exception as e:
                    print(fmt_warn(f"{label}: {e}"))
                    file_errors += 1

            if file_errors:
                print(fmt_warn(f"{label}: {file_errors} error(s) in {len(statements)} statements"))
                errors += file_errors
            else:
                print(fmt_ok(f"{label}: applied ({len(statements)} statements)"))

    driver.close()
    if errors:
        print(fmt_error(f"Init completed with {errors} error(s)"))
        return 1
    print(fmt_ok("Init complete"))
    return 0


def cmd_agent_list(args) -> int:
    """List all agents."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (a:Agent)
            OPTIONAL MATCH (a)-[:HAS_CAPABILITY]->(c:Capability)
            RETURN a.id AS id, a.name AS name, a.emoji AS emoji,
                   a.lifecycle AS lifecycle, a.last_heartbeat AS hb,
                   a.runtime AS runtime, count(c) AS caps
            ORDER BY a.name
        """)
        agents = [dict(r) for r in result]

    driver.close()

    if not agents:
        print("No agents found")
        return 0

    fmt_header("Agents")
    rows = [[
        a.get("emoji", "") + " " + a.get("name", a["id"]),
        a["id"], a.get("lifecycle", "?"), str(a["caps"]),
        a.get("runtime", "—"), str(a.get("hb", "—"))
    ] for a in agents]
    print(fmt_table(["Name", "ID", "Lifecycle", "Caps", "Runtime", "Last HB"], rows))
    return 0


def cmd_agent_info(args) -> int:
    """Show detailed agent info."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        # Agent details
        result = session.run("""
            MATCH (a:Agent {id: $id})
            RETURN a
        """, id=args.agent_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Agent '{args.agent_id}' not found"))
            driver.close()
            return 1

        agent = dict(record["a"])
        fmt_header(f"Agent: {agent.get('name', agent.get('id'))}")
        for k, v in sorted(agent.items()):
            print(fmt_kv(k, str(v)))

        # Capabilities
        result = session.run("""
            MATCH (a:Agent {id: $id})-[:HAS_CAPABILITY]->(c:Capability)
            RETURN c.id AS id, c.name AS name, c.kind AS kind
            ORDER BY c.name
        """, id=args.agent_id)
        caps = [dict(r) for r in result]

        if caps:
            print(fmt_section("Capabilities"))
            rows = [[c["id"], c.get("name", ""), c.get("kind", "?")] for c in caps]
            print(fmt_table(["ID", "Name", "Kind"], rows))

        # Recent Intents
        result = session.run("""
            MATCH (a:Agent {id: $id})-[:PROPOSED]->(i:Intent)
            RETURN i.id AS id, i.lifecycle AS lifecycle, i.action AS action,
                   i.submitted_at AS submitted
            ORDER BY i.submitted_at DESC
            LIMIT 5
        """, id=args.agent_id)
        intents = [dict(r) for r in result]

        if intents:
            print(fmt_section("Recent Intents"))
            rows = [[i["id"][:12] + "…", i.get("lifecycle", "?"),
                      i.get("action", "?"), str(i.get("submitted", "?"))] for i in intents]
            print(fmt_table(["ID", "Lifecycle", "Action", "Submitted"], rows))

    driver.close()
    return 0


def cmd_rule_list(args) -> int:
    """List all rules."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (r:Rule)
            RETURN r.id AS id, r.name AS name, r.lifecycle AS lifecycle,
                   r.priority AS priority, r.compiler_version AS cv,
                   r.compiled_at AS compiled_at, r.author AS author
            ORDER BY r.priority ASC
        """)
        rules = [dict(r) for r in result]

    driver.close()

    if not rules:
        print("No rules found")
        return 0

    fmt_header("Rules")
    rows = [[
        r["id"], r.get("name", ""), r.get("lifecycle", "?"),
        str(r.get("priority", "?")), r.get("author", "—"),
        r.get("cv", "—"), str(r.get("compiled_at", "—"))
    ] for r in rules]
    print(fmt_table(["ID", "Name", "Lifecycle", "Priority", "Author", "Compiler", "Compiled"], rows))
    return 0


def cmd_rule_compile(args) -> int:
    """Force-recompile a rule and show generated Python."""
    from hassaleh.engine.compiler import compile_rule, COMPILER_VERSION

    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (r:Rule {id: $id})
            RETURN r.rule_text AS rule_text, r.name AS name
        """, id=args.rule_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Rule '{args.rule_id}' not found"))
            driver.close()
            return 1

        rule_text = record["rule_text"]
        if not rule_text:
            print(fmt_error(f"Rule '{args.rule_id}' has no rule_text"))
            driver.close()
            return 1

        fmt_header(f"Compiling: {record.get('name', args.rule_id)}")

        print(fmt_section("GSL-Ops Source"))
        print(fmt_code(rule_text))

        try:
            python_source = compile_rule(rule_text, rule_id=args.rule_id)
        except Exception as e:
            print(fmt_error(f"Compilation failed: {e}"))
            driver.close()
            return 1

        print(fmt_section(f"Generated Python (compiler v{COMPILER_VERSION})"))
        print(fmt_code(python_source))

        # Update cache in graph
        if not args.dry_run:
            session.run("""
                MATCH (r:Rule {id: $id})
                SET r.compiled_python = $python,
                    r.compiled_at = datetime({timezone: 'UTC'}),
                    r.compiler_version = $version
            """, id=args.rule_id, python=python_source, version=COMPILER_VERSION)
            print(fmt_ok("Compiled and cached in graph"))
        else:
            print(fmt_warn("Dry run — not saved to graph"))

    driver.close()
    return 0


def cmd_intent_list(args) -> int:
    """List recent intents."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        filters = []
        params: dict[str, Any] = {"limit": args.limit or 20}

        if args.lifecycle:
            filters.append("i.lifecycle = $lifecycle")
            params["lifecycle"] = args.lifecycle
        if args.source:
            filters.append("i.source = $source")
            params["source"] = args.source

        where = "WHERE " + " AND ".join(filters) if filters else ""

        result = session.run(f"""
            MATCH (i:Intent)
            {where}
            RETURN i.id AS id, i.lifecycle AS lifecycle, i.action AS action,
                   i.submitted_at AS submitted, i.completed_at AS completed,
                   i.source AS source, i.exit_code AS exit_code,
                   i.error_reason AS error
            ORDER BY i.submitted_at DESC
            LIMIT $limit
        """, **params)
        intents = [dict(r) for r in result]

    driver.close()

    if not intents:
        print("No intents found")
        return 0

    fmt_header("Intents")
    rows = [[
        i["id"][:12] + "…", i.get("lifecycle", "?"), i.get("action", "?"),
        str(i.get("submitted", "?")), i.get("source", "agent"),
        str(i.get("exit_code", "—")), (i.get("error", "") or "")[:40]
    ] for i in intents]
    print(fmt_table(["ID", "Status", "Action", "Submitted", "Source", "Exit", "Error"], rows))
    return 0


def cmd_heartbeat(args) -> int:
    """Send agent heartbeat — updates last_heartbeat + lifecycle in graph."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (a:Agent {id: $id})
            SET a.last_heartbeat = datetime({timezone: 'UTC'}),
                a.lifecycle = 'running'
            RETURN a.id AS id, a.name AS name
        """, id=args.agent_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Agent '{args.agent_id}' not found"))
            driver.close()
            return 1

        print(fmt_ok(f"Heartbeat: {record.get('name', args.agent_id)} @ UTC now"))

    driver.close()
    return 0


def cmd_approve(args) -> int:
    """Approve an awaiting_approval intent."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (i:Intent {id: $id})
            RETURN i.lifecycle AS lifecycle
        """, id=args.intent_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Intent '{args.intent_id}' not found"))
            driver.close()
            return 1

        if record["lifecycle"] != "awaiting_approval":
            print(fmt_error(f"Intent is '{record['lifecycle']}', not 'awaiting_approval'"))
            driver.close()
            return 1

        session.run("""
            MATCH (i:Intent {id: $id})
            SET i.lifecycle = 'pending',
                i.approved_at = datetime({timezone: 'UTC'})
        """, id=args.intent_id)

        print(fmt_ok(f"Intent {args.intent_id} approved → pending"))

    driver.close()
    return 0


# ──────────────────────────────────────────────
# Argument Parser
# ──────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hassaleh",
        description="Hassaleh — Graph-native agent orchestration CLI",
    )

    # Global connection flags
    parser.add_argument("--uri", help="Neo4j URI (default: bolt://localhost:7690)")
    parser.add_argument("--user", help="Neo4j user (default: neo4j)")
    parser.add_argument("--password", help="Neo4j password")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # status
    sub.add_parser("status", help="Show system status")

    # init
    sub.add_parser("init", help="Initialize Neo4j schema + seed data")

    # agent
    agent_parser = sub.add_parser("agent", help="Agent management")
    agent_sub = agent_parser.add_subparsers(dest="agent_command")
    agent_sub.add_parser("list", help="List all agents")
    info_parser = agent_sub.add_parser("info", help="Show agent details")
    info_parser.add_argument("agent_id", help="Agent ID")

    # rule
    rule_parser = sub.add_parser("rule", help="Rule management")
    rule_sub = rule_parser.add_subparsers(dest="rule_command")
    rule_sub.add_parser("list", help="List all rules")
    compile_parser = rule_sub.add_parser("compile", help="Recompile a rule")
    compile_parser.add_argument("rule_id", help="Rule ID")
    compile_parser.add_argument("--dry-run", action="store_true", help="Don't save to graph")

    # intent
    intent_parser = sub.add_parser("intent", help="Intent management")
    intent_sub = intent_parser.add_subparsers(dest="intent_command")
    list_parser = intent_sub.add_parser("list", help="List intents")
    list_parser.add_argument("--lifecycle", help="Filter by lifecycle",
                             choices=["pending", "claimed", "running", "success",
                                      "failed", "rejected", "awaiting_approval"])
    list_parser.add_argument("--source", help="Filter by source",
                             choices=["agent", "rule"])
    list_parser.add_argument("--limit", type=int, default=20, help="Max results")

    # approve
    approve_parser = sub.add_parser("approve", help="Approve an intent")
    approve_parser.add_argument("intent_id", help="Intent ID")

    # heartbeat (for agents to report they're alive)
    hb_parser = sub.add_parser("heartbeat", help="Send agent heartbeat to graph")
    hb_parser.add_argument("agent_id", help="Agent ID")

    return parser


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "status": cmd_status,
        "init": cmd_init,
        "approve": cmd_approve,
        "heartbeat": cmd_heartbeat,
    }

    if args.command in commands:
        try:
            return commands[args.command](args)
        except Exception as e:
            print(fmt_error(str(e)))
            return 1

    if args.command == "agent":
        handler = {"list": cmd_agent_list, "info": cmd_agent_info}.get(
            args.agent_command)
        if not handler:
            print("Usage: hassaleh agent {list|info}")
            return 1
    elif args.command == "rule":
        handler = {"list": cmd_rule_list, "compile": cmd_rule_compile}.get(
            args.rule_command)
        if not handler:
            print("Usage: hassaleh rule {list|compile}")
            return 1
    elif args.command == "intent":
        handler = {"list": cmd_intent_list}.get(args.intent_command)
        if not handler:
            print("Usage: hassaleh intent {list}")
            return 1
    else:
        parser.print_help()
        return 0

    try:
        return handler(args)
    except Exception as e:
        print(fmt_error(str(e)))
        return 1


if __name__ == "__main__":
    sys.exit(main())
