import os
import uuid
import pytest
import pytest_asyncio
import logging
import subprocess
from unittest.mock import patch

from hassaleh.intent_sdk import IntentSDK
from hassaleh.intent_daemon import IntentDaemon
from hassaleh.auth import generate_api_key, hash_api_key, lookup_hash
from hassaleh.errors import CapabilityParamError

NEO4J_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7690")
NEO4J_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "hassaleh")

@pytest_asyncio.fixture
async def intent_sdk():
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    sdk = IntentSDK(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    await sdk.connect()
    
    async with sdk.driver.session() as session:
        await session.run("""
            MERGE (a:Agent {id: 'test-agent-il04'})
            SET a.name = 'Test Agent IL04',
                a.api_key_hash = $hash,
                a.api_key_lookup = $lookup,
                a.lifecycle = 'active'
        """, hash=hashed, lookup=lookup_hash(raw_key))
        await session.run("""
            MERGE (cap:Capability {id: 'exec-ls'})
            SET cap.name = 'List Directory',
                cap.kind = 'cli'
        """)
        await session.run("""
            MATCH (a:Agent {id: 'test-agent-il04'}), (cap:Capability {id: 'exec-ls'})
            MERGE (a)-[:HAS_CAPABILITY]->(cap)
        """)
    yield sdk, raw_key
    await sdk.close()

@pytest_asyncio.fixture
async def daemon():
    d = IntentDaemon(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, intent_timeout_sec=1)
    await d.connect()
    yield d
    await d.close()

@pytest.mark.asyncio
async def test_out_of_scope_path(intent_sdk, daemon, caplog):
    caplog.set_level(logging.ERROR)
    sdk, api_key = intent_sdk
    intent_id = await sdk.submit_intent(api_key, "exec-ls", {"path": "/tmp"})
    await daemon.process_intent(intent_id)
    res = await sdk.get_intent_result(api_key, intent_id)
    
    assert "Parameter validation failed [cid: " in res["error"]
    assert "/tmp" not in res["error"]
    
    cid = res["error"].split("[cid: ")[1].split("]")[0]
    assert cid in caplog.text

@pytest.mark.asyncio
async def test_nonexistent_path(intent_sdk, daemon, caplog):
    caplog.set_level(logging.ERROR)
    sdk, api_key = intent_sdk
    # /app is allowed base, so /app/does-not-exist passes submit-time check
    intent_id = await sdk.submit_intent(api_key, "exec-ls", {"path": "/app/does-not-exist-1234"})
    await daemon.process_intent(intent_id)
    res = await sdk.get_intent_result(api_key, intent_id)
    
    assert "Parameter validation failed [cid: " in res["error"]
    assert "does-not-exist" not in res["error"]
    
    cid = res["error"].split("[cid: ")[1].split("]")[0]
    assert cid in caplog.text

@pytest.mark.asyncio
async def test_non_directory_path(intent_sdk, daemon, caplog):
    caplog.set_level(logging.ERROR)
    sdk, api_key = intent_sdk
    # Mock validate_exec_ls_path to throw CapabilityParamError
    with patch("hassaleh.intent_daemon.validate_exec_ls_path") as mock_val:
        mock_val.side_effect = CapabilityParamError("Path is not a directory: /app/file.txt")
        intent_id = await sdk.submit_intent(api_key, "exec-ls", {"path": "/app/file.txt"})
        await daemon.process_intent(intent_id)
        
    res = await sdk.get_intent_result(api_key, intent_id)
    assert "Parameter validation failed [cid: " in res["error"]
    assert "/app/file.txt" not in res["error"]
    
    cid = res["error"].split("[cid: ")[1].split("]")[0]
    assert cid in caplog.text

@pytest.mark.asyncio
async def test_execute_ls_runtime_failure(intent_sdk, daemon, caplog):
    caplog.set_level(logging.ERROR)
    sdk, api_key = intent_sdk
    with patch("hassaleh.intent_daemon.execute_ls") as mock_exec, \
         patch("hassaleh.intent_daemon.validate_exec_ls_path") as mock_val:
        mock_val.return_value = "/app"
        mock_exec.side_effect = RuntimeError("ls: cannot access '/app': Permission denied")
        intent_id = await sdk.submit_intent(api_key, "exec-ls", {"path": "/app"})
        await daemon.process_intent(intent_id)
        
    res = await sdk.get_intent_result(api_key, intent_id)
    assert "Execution failed [cid: " in res["error"]
    assert "Permission denied" not in res["error"]
    
    cid = res["error"].split("[cid: ")[1].split("]")[0]
    assert cid in caplog.text

@pytest.mark.asyncio
async def test_execute_ls_timeout(intent_sdk, daemon, caplog):
    caplog.set_level(logging.ERROR)
    sdk, api_key = intent_sdk
    with patch("hassaleh.intent_daemon.execute_ls") as mock_exec, \
         patch("hassaleh.intent_daemon.validate_exec_ls_path") as mock_val:
        mock_val.return_value = "/app"
        mock_exec.side_effect = subprocess.TimeoutExpired(cmd=["ls", "-la", "/app"], timeout=30)
        intent_id = await sdk.submit_intent(api_key, "exec-ls", {"path": "/app"})
        await daemon.process_intent(intent_id)
        
    res = await sdk.get_intent_result(api_key, intent_id)
    assert "Execution timed out [cid: " in res["error"]
    
    cid = res["error"].split("[cid: ")[1].split("]")[0]
    assert cid in caplog.text
