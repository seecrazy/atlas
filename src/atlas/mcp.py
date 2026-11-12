"""Minimal MCP stdio server for ATLAS."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import ProjectConfig
from .db import connect
from .context import developer_context, module_context, system_context
from .store import (
    ScopeError,
    create_scope,
    file_context,
    health,
    impact,
    memories,
    references,
    scoped_search,
    search,
    review_memory,
    symbols,
)
from .router import route_request


PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "atlas_health",
        "description": "Show ATLAS index health and coverage.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "atlas_search",
        "description": "Search symbols and indexed file paths. Returns structural summaries, not code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_symbols",
        "description": "List symbols by query, file path, or repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "file_path": {"type": "string"},
                "repo": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_references",
        "description": "Find indexed import/reference edges targeting a symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_impact",
        "description": "Summarize downstream files and repositories affected by a symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_route",
        "description": "Classify a request into intent, role, recommended tools, and optional scope candidates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_memories",
        "description": "List memories with evidence references and drift status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["valid", "stale", "invalid"],
                },
                "layer": {
                    "type": "string",
                    "enum": ["structural", "semantic", "behavioral"],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_review_memory",
        "description": "Record an external reviewer decision that a memory is still valid or invalid.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer", "minimum": 1},
                "decision": {"type": "string", "enum": ["valid", "invalid"]},
                "reviewer": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["memory_id", "decision", "reviewer", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_file_context",
        "description": "Return the symbol and import outline for an indexed file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_system_context",
        "description": "Build a system-layer context packet with repos and cross-repo references.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subsystem": {"type": "string"},
                "repo": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_module_context",
        "description": "Build a module-layer context packet for one indexed file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_developer_context",
        "description": "Build a narrow developer context packet; it does not contain source text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "minLength": 1},
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_create_scope",
        "description": "Create a short-lived scope token. Intended for trusted orchestration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "ttl_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
            },
            "required": ["paths"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_scoped_search",
        "description": "Search only inside paths authorized by an ATLAS scope token.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "minLength": 1},
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["token", "query"],
            "additionalProperties": False,
        },
    },
]


def _text_result(payload: Any) -> dict:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            }
        ],
        "isError": False,
    }


def _error_result(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _dispatch_tool(conn, config: ProjectConfig, name: str, args: dict) -> Any:
    if name == "atlas_health":
        return health(conn)
    if name == "atlas_search":
        return search(conn, args["query"], limit=args.get("limit"))
    if name == "atlas_symbols":
        return symbols(
            conn,
            query=args.get("query"),
            file_path=args.get("file_path"),
            repo=args.get("repo"),
            limit=args.get("limit"),
        )
    if name == "atlas_references":
        return references(conn, args["symbol"], limit=args.get("limit"))
    if name == "atlas_impact":
        return impact(
            conn,
            args["symbol"],
            limit=args.get("limit"),
            min_confidence=args.get("min_confidence"),
        )
    if name == "atlas_file_context":
        return file_context(conn, args["file_path"], limit=args.get("limit"))
    if name == "atlas_system_context":
        return system_context(
            conn,
            subsystem=args.get("subsystem"),
            repo=args.get("repo"),
            limit=args.get("limit"),
        )
    if name == "atlas_module_context":
        return module_context(conn, args["file_path"], limit=args.get("limit"))
    if name == "atlas_developer_context":
        return developer_context(
            conn,
            args["file_path"],
            symbol=args.get("symbol"),
            limit=args.get("limit"),
        )
    if name == "atlas_create_scope":
        return create_scope(
            conn,
            config,
            args["paths"],
            ttl_seconds=args.get("ttl_seconds", 900),
            created_by="mcp",
        )
    if name == "atlas_scoped_search":
        return scoped_search(
            conn,
            args["token"],
            args["query"],
            limit=args.get("limit"),
            actor="mcp",
        )
    if name == "atlas_route":
        return route_request(conn, args["question"], limit=args.get("limit", 10))
    if name == "atlas_memories":
        return memories(
            conn,
            status=args.get("status"),
            layer=args.get("layer"),
            limit=args.get("limit"),
        )
    if name == "atlas_review_memory":
        return review_memory(
            conn,
            args["memory_id"],
            args["decision"],
            reviewer=args["reviewer"],
            reason=args["reason"],
        )
    raise KeyError(name)


class McpServer:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.conn = connect(config.db_path)

    def close(self) -> None:
        self.conn.close()

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "atlas",
                        "version": "0.1.0",
                    },
                },
            }
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": TOOLS},
            }
        if method == "tools/call":
            params = message.get("params") or {}
            tool_name = params.get("name")
            tool_args = params.get("arguments") or {}
            try:
                if not isinstance(tool_args, dict):
                    raise ValueError("tool arguments must be an object")
                payload = _dispatch_tool(self.conn, self.config, tool_name, tool_args)
                return {"jsonrpc": "2.0", "id": request_id, "result": _text_result(payload)}
            except ScopeError as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _error_result(str(exc)),
                }
            except (FileNotFoundError, KeyError, ValueError, TypeError) as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _error_result(str(exc)),
                }
            except Exception as exc:  # MCP must stay alive for subsequent requests.
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _error_result(f"internal tool error: {exc}"),
                }

        if request_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return None

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"parse error: {exc}"},
                }
            else:
                if not isinstance(message, dict):
                    response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "request must be an object"},
                    }
                else:
                    response = self.handle(message)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)


def run_mcp(config: ProjectConfig) -> None:
    server = McpServer(config)
    try:
        server.run()
    finally:
        server.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-mcp", description="ATLAS MCP stdio server")
    parser.add_argument("--project-root", required=True, help="ATLAS project root")
    args = parser.parse_args(argv)
    try:
        from .config import load_config

        config = load_config(args.project_root)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    run_mcp(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

