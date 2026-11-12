import json

import pytest

from atlas.config import load_config
from atlas.db import connect
from atlas.mcp import McpServer


def call(server: McpServer, method: str, params=None):
    message = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


def test_mcp_initialize_and_tools_list(project_root):
    config = load_config(project_root)
    server = McpServer(config)
    try:
        initialized = call(server, "initialize", {})
        assert initialized["result"]["serverInfo"]["name"] == "atlas"
        tools = call(server, "tools/list", {})
        names = {tool["name"] for tool in tools["result"]["tools"]}
        assert {
            "atlas_search",
            "atlas_references",
            "atlas_impact",
            "atlas_impact_chain",
            "atlas_converge",
            "atlas_system_context",
            "atlas_module_context",
            "atlas_developer_context",
            "atlas_create_scope",
            "atlas_scoped_search",
            "atlas_route",
            "atlas_memories",
            "atlas_review_memory",
        } <= names
    finally:
        server.close()


def test_mcp_search_and_scope(project_root):
    config = load_config(project_root)
    server = McpServer(config)
    try:
        response = call(
            server,
            "tools/call",
            {"name": "atlas_search", "arguments": {"query": "execute_with_backoff"}},
        )
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload[0]["file"] == "demo/retry/handler.py"

        system_response = call(
            server,
            "tools/call",
            {"name": "atlas_system_context", "arguments": {}},
        )
        system_payload = json.loads(system_response["result"]["content"][0]["text"])
        assert system_payload["layer"] == "system"
        assert system_payload["repos"][0]["name"] == "demo"

        created = call(
            server,
            "tools/call",
            {"name": "atlas_create_scope", "arguments": {"paths": ["demo/client"]}},
        )
        scope_payload = json.loads(created["result"]["content"][0]["text"])
        scoped = call(
            server,
            "tools/call",
            {
                "name": "atlas_scoped_search",
                "arguments": {"token": scope_payload["token"], "query": "Retry"},
            },
        )
        scoped_payload = json.loads(scoped["result"]["content"][0]["text"])
        assert scoped_payload
        assert all(item["file"].startswith("demo/client/") for item in scoped_payload)

        invalid = call(
            server,
            "tools/call",
            {
                "name": "atlas_scoped_search",
                "arguments": {"token": "not-a-token", "query": "Retry"},
            },
        )
        assert invalid["result"]["isError"] is True
    finally:
        server.close()

