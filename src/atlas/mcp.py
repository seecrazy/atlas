"""Minimal MCP stdio server for ATLAS."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from .config import ProjectConfig
from . import __version__
from .db import connect
from .context import developer_context, module_context, system_context
from .store import (
    ScopeError,
    create_scope,
    file_context,
    health,
    impact,
    memories,
    impact_chain,
    references,
    scoped_search,
    search,
    hybrid_search,
    review_memory,
    symbols,
)
from .router import route_request
from .propagation import export_propagation
from .pipeline import run_pipeline
from .synthesis import converge_findings
from .memory import generate_semantic_memories, auto_review_stale_memories, extract_revert_lessons
from .architecture import sync_contracts, list_contracts, contract_impact, ingest_architecture_docs, list_architecture_docs
from .agents import run_layered_agents
from .kernel import list_approvals, decide_approval
from .changes import propose_patch, decide_patch, apply_patch, list_patches, propose_publication, publish_patch
from .evolution import distill_strategy_rules, evaluate_variant, list_variants, propose_promotion, promote_variant, propose_rollback, rollback_variant
from .evaluation import run_production_evaluation
from .kernel import Budget
from .operations import new_trace_id, record_metric


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
        "name": "atlas_impact_chain",
        "description": "Return direct references and one module-level upstream hop.",
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
        "name": "atlas_export_propagation",
        "description": "Report downstream references for recently changed exported symbols.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_pipeline",
        "description": "Run deterministic route, context, worker, and convergence pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 1},
                "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["question"],
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
        "name": "atlas_generate_memories",
        "description": "Generate semantic memories for files, repos, and subsystems.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "use_llm": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_auto_review_memories",
        "description": "Automatically review stale memories against current evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_extract_lessons",
        "description": "Extract behavioral lessons from git revert commits.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_sync_contracts",
        "description": "Sync explicit architecture contracts from project config.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "atlas_contracts",
        "description": "List architecture contracts.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}},
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_contract_impact",
        "description": "Evaluate references affected by a named contract.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_index_architecture_docs",
        "description": "Index configured architecture documents and ADRs.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "atlas_architecture_docs",
        "description": "List indexed architecture documents.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_layered_agents",
        "description": "Run architect, system, module, and developer role agents.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 1},
                "use_llm": {"type": "boolean"},
                "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_approvals",
        "description": "List kernel approval requests.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "approved", "rejected"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_decide_approval",
        "description": "Approve or reject a kernel action.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "approval_id": {"type": "integer", "minimum": 1},
                "decision": {"type": "string", "enum": ["approved", "rejected"]},
                "approver": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["approval_id", "decision", "approver", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_patches",
        "description": "List proposed patches without full text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "approved", "rejected", "applied"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_propose_patch",
        "description": "Propose an exact-text patch and request high-risk approval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "minLength": 1},
                "old_text": {"type": "string", "minLength": 1},
                "new_text": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "created_by": {"type": "string", "minLength": 1},
            },
            "required": ["file_path", "old_text", "new_text", "title", "created_by"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_decide_patch",
        "description": "Approve or reject a proposed patch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "patch_key": {"type": "string", "minLength": 1},
                "decision": {"type": "string", "enum": ["approved", "rejected"]},
                "approver": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["patch_key", "decision", "approver", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_apply_patch",
        "description": "Apply an approved patch, optionally with a sandbox test command.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "patch_key": {"type": "string", "minLength": 1},
                "applied_by": {"type": "string", "minLength": 1},
                "test_command": {"type": "string"},
            },
            "required": ["patch_key", "applied_by"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_distill_strategies",
        "description": "Distill strategy rules from recent router audits.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_evaluate_variant",
        "description": "Evaluate an offline evolution variant against frozen cases.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "variant_key": {"type": "string", "minLength": 1},
                "record": {"type": "boolean"},
            },
            "required": ["variant_key"],
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
        "name": "atlas_converge",
        "description": "Converge role worker findings into an ordered upward synthesis.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 1},
                "findings": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {
                                "type": "string",
                                "enum": ["developer", "module", "system", "architect"],
                            },
                            "claim": {"type": "string", "minLength": 1},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "file": {"type": "string", "minLength": 1},
                                        "line": {"type": "integer", "minimum": 1},
                                        "repo": {"type": "string"},
                                        "note": {"type": "string"},
                                    },
                                    "required": ["file"],
                                    "additionalProperties": False,
                                },
                            },
                            "warnings": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "recommended_actions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["role", "claim", "evidence"],
                        "additionalProperties": False,
                    },
                },
                "route": {"type": "object"},
                "impact_symbol": {"type": "string", "minLength": 1},
                "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["question", "findings"],
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
    {
        "name": "atlas_hybrid_search",
        "description": "Use SQLite FTS5 hybrid ranking over symbols, memories, contracts, and docs.",
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
        "name": "atlas_propose_publication",
        "description": "Request high-risk approval to publish an applied patch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "patch_key": {"type": "string", "minLength": 1},
                "remote": {"type": "string", "minLength": 1},
                "base_branch": {"type": "string", "minLength": 1},
                "branch": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "body": {"type": "string"},
                "requested_by": {"type": "string", "minLength": 1},
            },
            "required": ["patch_key", "remote", "base_branch", "branch", "title", "requested_by"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_publish_patch",
        "description": "Publish an approved applied patch to Git and optionally create a PR.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "patch_key": {"type": "string", "minLength": 1},
                "remote": {"type": "string", "minLength": 1},
                "base_branch": {"type": "string", "minLength": 1},
                "branch": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "body": {"type": "string"},
                "approval_key": {"type": "string", "minLength": 1},
                "published_by": {"type": "string", "minLength": 1},
                "push": {"type": "boolean"},
                "create_pr": {"type": "boolean"},
            },
            "required": ["patch_key", "remote", "base_branch", "branch", "title", "approval_key", "published_by"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_production_evaluation",
        "description": "Run frozen semantic, agent, and router evaluation harness.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cases_path": {"type": "string"},
                "use_llm": {"type": "boolean"},
                "record": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_propose_variant_promotion",
        "description": "Request high-risk approval to promote an accepted strategy variant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "variant_key": {"type": "string", "minLength": 1},
                "requested_by": {"type": "string", "minLength": 1},
            },
            "required": ["variant_key", "requested_by"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_promote_variant",
        "description": "Promote an approved strategy variant into runtime strategy rules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "variant_key": {"type": "string", "minLength": 1},
                "approval_key": {"type": "string", "minLength": 1},
                "promoted_by": {"type": "string", "minLength": 1},
            },
            "required": ["variant_key", "approval_key", "promoted_by"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_propose_variant_rollback",
        "description": "Request high-risk approval to roll back a promoted strategy variant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "variant_key": {"type": "string", "minLength": 1},
                "requested_by": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["variant_key", "requested_by", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "atlas_rollback_variant",
        "description": "Roll back an approved promoted strategy variant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "variant_key": {"type": "string", "minLength": 1},
                "approval_key": {"type": "string", "minLength": 1},
                "rolled_back_by": {"type": "string", "minLength": 1},
            },
            "required": ["variant_key", "approval_key", "rolled_back_by"],
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


def _validate_tool_arguments(name: str, args: dict) -> None:
    schema = next((tool["inputSchema"] for tool in TOOLS if tool["name"] == name), None)
    if schema is None:
        return
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unknown = set(args) - set(properties)
        if unknown:
            raise ValueError(f"unknown arguments: {', '.join(sorted(unknown))}")
    for key in schema.get("required", []):
        if key not in args:
            raise ValueError(f"missing required argument: {key}")
    for key, value in args.items():
        if key not in properties:
            continue
        rule = properties[key]
        expected = rule.get("type")
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
        }
        if expected in checks and not checks[expected](value):
            raise ValueError(f"argument {key} must be {expected}")
        if expected == "array" and isinstance(value, list):
            if "minItems" in rule and len(value) < rule["minItems"]:
                raise ValueError(f"argument {key} requires at least {rule['minItems']} items")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"argument {key} must be one of: {', '.join(rule['enum'])}")
        if "minLength" in rule and isinstance(value, str) and len(value) < rule["minLength"]:
            raise ValueError(f"argument {key} is too short")
        if "minimum" in rule and isinstance(value, (int, float)) and value < rule["minimum"]:
            raise ValueError(f"argument {key} is below minimum {rule['minimum']}")
        if "maximum" in rule and isinstance(value, (int, float)) and value > rule["maximum"]:
            raise ValueError(f"argument {key} is above maximum {rule['maximum']}")


def _dispatch_tool(conn, config: ProjectConfig, name: str, args: dict) -> Any:
    _validate_tool_arguments(name, args)
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
    if name == "atlas_impact_chain":
        return impact_chain(
            conn,
            args["symbol"],
            limit=args.get("limit"),
            min_confidence=args.get("min_confidence"),
        )
    if name == "atlas_export_propagation":
        return export_propagation(
            conn,
            limit=args.get("limit"),
            min_confidence=args.get("min_confidence"),
        )
    if name == "atlas_pipeline":
        return run_pipeline(
            conn,
            config,
            args["question"],
            min_confidence=args.get("min_confidence", 0.65),
            limit=args.get("limit", 50),
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
    if name == "atlas_converge":
        return converge_findings(
            conn,
            args["question"],
            args["findings"],
            route=args.get("route"),
            impact_symbol=args.get("impact_symbol"),
            min_confidence=args.get("min_confidence", 0.65),
            limit=args.get("limit", 50),
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
    if name == "atlas_hybrid_search":
        return hybrid_search(conn, args["query"], limit=args.get("limit"))
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
    if name == "atlas_generate_memories":
        return generate_semantic_memories(
            conn,
            config,
            scope=args.get("scope"),
            use_llm=args.get("use_llm", True),
        )
    if name == "atlas_auto_review_memories":
        return auto_review_stale_memories(conn, limit=args.get("limit", 100))
    if name == "atlas_extract_lessons":
        return extract_revert_lessons(conn, config, limit=args.get("limit", 20))
    if name == "atlas_sync_contracts":
        return sync_contracts(conn, config)
    if name == "atlas_contracts":
        return list_contracts(conn, limit=args.get("limit", 100))
    if name == "atlas_contract_impact":
        return contract_impact(
            conn,
            args["name"],
            limit=args.get("limit", 100),
            min_confidence=args.get("min_confidence", 0.65),
        )
    if name == "atlas_index_architecture_docs":
        return ingest_architecture_docs(conn, config)
    if name == "atlas_architecture_docs":
        return list_architecture_docs(conn, kind=args.get("kind"), limit=args.get("limit", 100))
    if name == "atlas_layered_agents":
        return run_layered_agents(
            conn,
            config,
            args["question"],
            use_llm=args.get("use_llm", True),
            min_confidence=args.get("min_confidence", 0.65),
            limit=args.get("limit", 50),
        )
    if name == "atlas_approvals":
        return list_approvals(conn, status=args.get("status"), limit=args.get("limit", 50))
    if name == "atlas_decide_approval":
        return decide_approval(
            conn,
            args["approval_id"],
            args["decision"],
            approver=args["approver"],
            reason=args["reason"],
        )
    if name == "atlas_patches":
        return list_patches(conn, status=args.get("status"), limit=args.get("limit", 50))
    if name == "atlas_propose_patch":
        return propose_patch(
            conn,
            config,
            args["file_path"],
            old_text=args["old_text"],
            new_text=args["new_text"],
            title=args["title"],
            created_by=args["created_by"],
        )
    if name == "atlas_decide_patch":
        return decide_patch(
            conn,
            args["patch_key"],
            args["decision"],
            approver=args["approver"],
            reason=args["reason"],
        )
    if name == "atlas_apply_patch":
        return apply_patch(
            conn,
            config,
            args["patch_key"],
            applied_by=args["applied_by"],
            test_command=args.get("test_command"),
        )
    if name == "atlas_distill_strategies":
        return distill_strategy_rules(conn, limit=args.get("limit", 100))
    if name == "atlas_evaluate_variant":
        return evaluate_variant(conn, config, args["variant_key"], record=args.get("record", True))
    if name == "atlas_propose_publication":
        return propose_publication(
            conn,
            config,
            args["patch_key"],
            remote=args["remote"],
            base_branch=args["base_branch"],
            branch=args["branch"],
            title=args["title"],
            body=args.get("body", ""),
            requested_by=args["requested_by"],
        )
    if name == "atlas_publish_patch":
        return publish_patch(
            conn,
            config,
            args["patch_key"],
            remote=args["remote"],
            base_branch=args["base_branch"],
            branch=args["branch"],
            title=args["title"],
            body=args.get("body", ""),
            approval_key=args["approval_key"],
            published_by=args["published_by"],
            push=args.get("push", True),
            create_pr=args.get("create_pr", True),
        )
    if name == "atlas_production_evaluation":
        return run_production_evaluation(
            conn,
            config,
            cases_path=args.get("cases_path"),
            use_llm=args.get("use_llm", False),
            record=args.get("record", True),
        )
    if name == "atlas_propose_variant_promotion":
        return propose_promotion(conn, args["variant_key"], requested_by=args["requested_by"])
    if name == "atlas_promote_variant":
        return promote_variant(
            conn,
            args["variant_key"],
            approval_key=args["approval_key"],
            promoted_by=args["promoted_by"],
        )
    if name == "atlas_propose_variant_rollback":
        return propose_rollback(
            conn,
            args["variant_key"],
            requested_by=args["requested_by"],
            reason=args["reason"],
        )
    if name == "atlas_rollback_variant":
        return rollback_variant(
            conn,
            args["variant_key"],
            approval_key=args["approval_key"],
            rolled_back_by=args["rolled_back_by"],
        )
    raise KeyError(name)


class McpServer:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.conn = connect(config.db_path)
        self.budget = Budget.from_config(config)

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
                        "version": __version__,
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
            trace_id = new_trace_id()
            started = time.perf_counter()
            status = "completed"
            try:
                if not isinstance(tool_args, dict):
                    raise ValueError("tool arguments must be an object")
                self.budget.spend(calls=1, tokens=max(1, len(json.dumps(tool_args)) // 4))
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
                status = "failed"
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _error_result(f"internal tool error: {exc}"),
                }
            finally:
                record_metric(
                    self.conn,
                    trace_id,
                    f"mcp.{tool_name}",
                    actor="mcp",
                    status=status,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    result_count=0,
                    payload={"arguments": tool_args},
                )
                self.conn.commit()

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

