#!/usr/bin/env python3.13
"""Hassaleh Dummy Agent — Sprint 1 end-to-end proof.

Proves the full Intent loop:
  Agent → SDK → Intent → Daemon → OS action → feedback → Agent reads result

Steps:
  1. Connect to Neo4j via SDK
  2. Query assigned tasks
  3. Submit Intent to execute 'exec-ls' capability (ls -la on workspace)
  4. Poll Intent until completion
  5. Verify stdout contains 'schema.cypher'

Usage:
  NEO4J_URI=bolt://localhost:7690 python3.13 -m hassaleh.agent_dummy

Reference: docs/CONCEPT.md v1.2, Phase 3
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from hassaleh.sdk import HassalehSDK

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [dummy-agent] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("hassaleh.agent_dummy")

AGENT_ID = "dione"
TASK_ID = "mvp-test-ls"
CAPABILITY_ID = "exec-ls"
EXPECTED_FILE = "schema.cypher"


async def main() -> int:
    """Run the dummy agent end-to-end test."""

    async with HassalehSDK() as sdk:

        # ── Step 1: Check agent info ──
        log.info("Step 1: Checking agent info...")
        agent = await sdk.agent_info(AGENT_ID)
        if not agent:
            log.error(f"Agent '{AGENT_ID}' not found in graph!")
            return 1
        log.info(f"Agent: {agent['name']} {agent['emoji']} (lifecycle: {agent['lifecycle']})")

        # ── Step 2: Query assigned tasks ──
        log.info("Step 2: Querying assigned tasks...")
        tasks = await sdk.my_tasks(AGENT_ID)
        log.info(f"Found {len(tasks)} task(s)")

        target_task = None
        for t in tasks:
            log.info(f"  - [{t['lifecycle']}] {t['name']}")
            if t["id"] == TASK_ID:
                target_task = t

        if not target_task:
            log.error(f"Task '{TASK_ID}' not assigned to agent!")
            return 1

        if target_task["lifecycle"] != "pending":
            log.warning(f"Task lifecycle is '{target_task['lifecycle']}', expected 'pending'")

        # ── Step 3: Submit Intent to execute ls -la ──
        log.info("Step 3: Submitting Intent to execute 'exec-ls' capability...")
        intent_id = await sdk.submit_intent(
            agent_id=AGENT_ID,
            action="execute_capability",
            capability_id=CAPABILITY_ID,
            value=json.dumps({"args": "-la"}),
        )
        log.info(f"Intent submitted: {intent_id}")

        # ── Step 4: Poll until completion ──
        log.info("Step 4: Waiting for Intent completion...")
        try:
            result = await sdk.wait_for_intent(
                intent_id,
                timeout_sec=30.0,
                poll_interval=0.5,
            )
        except TimeoutError:
            log.error("Intent timed out after 30s — is the Daemon running?")
            return 1

        log.info(f"Intent completed: lifecycle={result['lifecycle']}")

        if result["lifecycle"] == "failed":
            log.error(f"Intent failed: {result.get('error_reason', 'unknown')}")
            if result.get("stderr"):
                log.error(f"stderr: {result['stderr'][:500]}")
            return 1

        if result["lifecycle"] == "rejected":
            log.error(f"Intent rejected: {result.get('error_reason', 'unknown')}")
            return 1

        # ── Step 5: Verify stdout ──
        log.info("Step 5: Verifying output...")
        stdout = result.get("stdout", "") or ""
        log.info(f"stdout ({len(stdout)} bytes):\n{stdout[:1000]}")

        if EXPECTED_FILE in stdout:
            log.info(f"✅ SUCCESS: '{EXPECTED_FILE}' found in output!")
            return 0
        else:
            log.error(f"❌ FAIL: '{EXPECTED_FILE}' NOT found in output!")
            return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
