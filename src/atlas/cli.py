"""Command line entry points for ATLAS."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .config import ConfigError, load_config, write_default_config
from .db import connect
from .indexer import index_project
from .store import health, hybrid_search
from .report import health_report
from .router import evaluate_router
from .propagation import export_propagation
from .pipeline import run_pipeline
from .daemon import run_daemon
from .agents import run_layered_agents
from .memory import generate_semantic_memories, auto_review_stale_memories, extract_revert_lessons
from .architecture import sync_contracts, list_contracts, contract_impact, ingest_architecture_docs, list_architecture_docs
from .kernel import list_approvals, decide_approval
from .changes import propose_patch, decide_patch, apply_patch, list_patches, propose_publication, publish_patch
from .evolution import distill_strategy_rules, create_variant, evaluate_variant, list_variants, propose_promotion, promote_variant, propose_rollback, rollback_variant
from .evaluation import run_production_evaluation
from .daemon import run_watch_daemon


def print_json(value: dict | list) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def command_init(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.project_root).expanduser().resolve()
    path = write_default_config(root, overwrite=args.overwrite)
    print(f"Initialized ATLAS config: {path}")
    return 0


def command_index(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    stats = index_project(config, full=args.full, include_workspace=not args.no_workspace)
    print_json(stats)
    return 0


def command_health(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        print_json(health(conn))
    finally:
        conn.close()
    return 0


def command_serve(args: argparse.Namespace) -> int:
    # Import lazily so CLI operations do not load MCP runtime internals.
    from .mcp import run_mcp

    config = load_config(args.project_root)
    run_mcp(config)
    return 0


def command_report(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        report = health(conn)
        if args.format == "markdown":
            rendered = health_report(report)
        else:
            rendered = None
    finally:
        conn.close()
    if rendered is not None:
        if args.output:
            output = pathlib.Path(args.output).expanduser()
            if args.create_parents:
                output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            print(f"Wrote health report: {output}")
        else:
            print(rendered, end="")
    else:
        print_json(report)
    return 0


def command_validate_router(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        metrics = evaluate_router(conn, cases_path=args.cases, record=not args.no_record)
    finally:
        conn.close()
    print_json(metrics)
    return 0


def command_run(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        result = run_pipeline(
            conn,
            args.question,
            min_confidence=args.min_confidence,
            limit=args.limit,
        )
    finally:
        conn.close()
    print_json(result)
    return 0


def command_propagate(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        result = export_propagation(
            conn,
            limit=args.limit,
            min_confidence=args.min_confidence,
        )
    finally:
        conn.close()
    print_json(result)
    return 0


def command_daemon(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    if args.watch:
        result = run_watch_daemon(
            config,
            debounce_ms=args.debounce_ms,
            full=args.full,
            include_workspace=not args.no_workspace,
            on_cycle=lambda cycle: print_json(cycle),
        )
        print_json(result)
        return 0
    result = run_daemon(
        config,
        interval=args.interval,
        max_iterations=args.max_iterations,
        full=args.full,
        include_workspace=not args.no_workspace,
        on_cycle=lambda cycle: print_json(cycle),
    )
    print_json(result)
    return 0


def command_search(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        if args.hybrid:
            result = hybrid_search(conn, args.query, limit=args.limit)
        else:
            from .store import search

            result = search(conn, args.query, limit=args.limit)
    finally:
        conn.close()
    print_json(result)
    return 0


def command_evaluate(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        result = run_production_evaluation(
            conn,
            config,
            cases_path=args.cases,
            use_llm=not args.no_llm,
            record=not args.no_record,
        )
    finally:
        conn.close()
    print_json(result)
    return 0


def command_agents(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        result = run_layered_agents(
            conn,
            config,
            args.question,
            use_llm=not args.no_llm,
            min_confidence=args.min_confidence,
            limit=args.limit,
        )
    finally:
        conn.close()
    print_json(result)
    return 0


def command_memory(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        if args.memory_command == "generate":
            result = generate_semantic_memories(conn, config, scope=args.scope, use_llm=not args.no_llm)
        elif args.memory_command == "auto-review":
            result = auto_review_stale_memories(conn, limit=args.limit)
        elif args.memory_command == "lessons":
            result = extract_revert_lessons(conn, config, limit=args.limit)
        else:
            raise ValueError("unknown memory action")
    finally:
        conn.close()
    print_json(result)
    return 0


def command_architecture(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        if args.architecture_command == "sync":
            result = sync_contracts(conn, config)
        elif args.architecture_command == "contracts":
            result = list_contracts(conn, limit=args.limit)
        elif args.architecture_command == "impact":
            result = contract_impact(
                conn,
                args.name,
                limit=args.limit,
                min_confidence=args.min_confidence,
            )
        elif args.architecture_command == "docs-index":
            result = ingest_architecture_docs(conn, config)
        elif args.architecture_command == "docs":
            result = list_architecture_docs(conn, kind=args.kind, limit=args.limit)
        else:
            raise ValueError("unknown architecture action")
    finally:
        conn.close()
    print_json(result)
    return 0


def command_approvals(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        if args.approvals_command == "list":
            result = list_approvals(conn, status=args.status, limit=args.limit)
        elif args.approvals_command == "decide":
            result = decide_approval(
                conn,
                args.approval_id,
                args.decision,
                approver=args.approver,
                reason=args.reason,
            )
        else:
            raise ValueError("unknown approvals action")
    finally:
        conn.close()
    print_json(result)
    return 0


def command_patch(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        if args.patch_command == "propose":
            result = propose_patch(
                conn,
                config,
                args.file,
                old_text=args.old_text,
                new_text=args.new_text,
                title=args.title,
                created_by=args.created_by,
            )
        elif args.patch_command == "decide":
            result = decide_patch(
                conn,
                args.patch_key,
                args.decision,
                approver=args.approver,
                reason=args.reason,
            )
        elif args.patch_command == "apply":
            result = apply_patch(
                conn,
                args.patch_key,
                applied_by=args.applied_by,
                test_command=args.test_command,
            )
        elif args.patch_command == "list":
            result = list_patches(conn, status=args.status, limit=args.limit)
        elif args.patch_command == "propose-publish":
            result = propose_publication(
                conn,
                config,
                args.patch_key,
                remote=args.remote,
                base_branch=args.base_branch,
                branch=args.branch,
                title=args.title,
                body=args.body,
                requested_by=args.requested_by,
            )
        elif args.patch_command == "publish":
            result = publish_patch(
                conn,
                config,
                args.patch_key,
                remote=args.remote,
                base_branch=args.base_branch,
                branch=args.branch,
                title=args.title,
                body=args.body,
                approval_key=args.approval_key,
                published_by=args.published_by,
                push=not args.no_push,
                create_pr=not args.no_pr,
            )
        else:
            raise ValueError("unknown patch action")
    finally:
        conn.close()
    print_json(result)
    return 0


def command_evolution(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    conn = connect(config.db_path)
    try:
        if args.evolution_command == "distill":
            result = distill_strategy_rules(conn, limit=args.limit)
        elif args.evolution_command == "create":
            payload = json.loads(args.payload)
            result = create_variant(
                conn,
                object_type=args.object_type,
                name=args.name,
                payload=payload,
                parent_key=args.parent_key,
            )
        elif args.evolution_command == "evaluate":
            result = evaluate_variant(conn, config, args.variant_key, record=not args.no_record)
        elif args.evolution_command == "list":
            result = list_variants(conn, status=args.status, limit=args.limit)
        elif args.evolution_command == "propose-promotion":
            result = propose_promotion(conn, args.variant_key, requested_by=args.requested_by)
        elif args.evolution_command == "promote":
            result = promote_variant(
                conn,
                args.variant_key,
                approval_key=args.approval_key,
                promoted_by=args.promoted_by,
            )
        elif args.evolution_command == "propose-rollback":
            result = propose_rollback(
                conn,
                args.variant_key,
                requested_by=args.requested_by,
                reason=args.reason,
            )
        elif args.evolution_command == "rollback":
            result = rollback_variant(
                conn,
                args.variant_key,
                approval_key=args.approval_key,
                rolled_back_by=args.rolled_back_by,
            )
        else:
            raise ValueError("unknown evolution action")
    finally:
        conn.close()
    print_json(result)
    return 0


def command_serve_http(args: argparse.Namespace) -> int:
    from .server import run_http_server

    run_http_server(args.project_root, args.host, args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas", description="Layered project knowledge system")
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="initialize an ATLAS project")
    init_parser.add_argument("project_root", nargs="?", default=".", help="target project root")
    init_parser.add_argument("--overwrite", action="store_true", help="overwrite existing config")
    init_parser.set_defaults(handler=command_init)

    index_parser = commands.add_parser("index", help="index configured repositories")
    index_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    index_parser.add_argument(
        "--full",
        action="store_true",
        help="force a full scan instead of Git commit-diff candidates",
    )
    index_parser.add_argument(
        "--no-workspace",
        action="store_true",
        help="exclude uncommitted working-tree changes from incremental candidates",
    )
    index_parser.set_defaults(handler=command_index)

    health_parser = commands.add_parser("health", help="show index health")
    health_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    health_parser.set_defaults(handler=command_health)

    report_parser = commands.add_parser("report", help="generate a project health report")
    report_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    report_parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="report output format",
    )
    report_parser.add_argument("--output", help="write output to this file")
    report_parser.add_argument(
        "--create-parents",
        action="store_true",
        help="create missing output parent directories",
    )
    report_parser.set_defaults(handler=command_report)

    validate_parser = commands.add_parser("validate-router", help="evaluate the frozen router validation set")
    validate_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    validate_parser.add_argument("--cases", help="custom JSON validation cases")
    validate_parser.add_argument(
        "--no-record",
        action="store_true",
        help="do not store validation metrics",
    )
    validate_parser.set_defaults(handler=command_validate_router)

    run_parser = commands.add_parser("run", help="run the deterministic end-to-end pipeline")
    run_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    run_parser.add_argument("question", help="developer question")
    run_parser.add_argument("--min-confidence", type=float, default=0.65)
    run_parser.add_argument("--limit", type=int, default=50)
    run_parser.set_defaults(handler=command_run)

    propagate_parser = commands.add_parser("propagate", help="report changed exported symbol propagation")
    propagate_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    propagate_parser.add_argument("--limit", type=int, default=50)
    propagate_parser.add_argument("--min-confidence", type=float, default=0.65)
    propagate_parser.set_defaults(handler=command_propagate)

    search_parser = commands.add_parser("search", help="search indexed symbols, memories, and docs")
    search_parser.add_argument("project_root", nargs="?", default=".")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=50)
    search_parser.add_argument("--hybrid", action="store_true", help="use SQLite FTS5 hybrid scoring")
    search_parser.set_defaults(handler=command_search)

    evaluate_parser = commands.add_parser("evaluate", help="run production evaluation harness")
    evaluate_parser.add_argument("project_root", nargs="?", default=".")
    evaluate_parser.add_argument("--cases", help="JSON evaluation cases")
    evaluate_parser.add_argument("--no-llm", action="store_true")
    evaluate_parser.add_argument("--no-record", action="store_true")
    evaluate_parser.set_defaults(handler=command_evaluate)

    daemon_parser = commands.add_parser("daemon", help="run foreground incremental indexing")
    daemon_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    daemon_parser.add_argument("--interval", type=float, default=5.0)
    daemon_parser.add_argument("--max-iterations", type=int)
    daemon_parser.add_argument("--watch", action="store_true", help="use OS file notifications via watchfiles")
    daemon_parser.add_argument("--debounce-ms", type=int, default=300)
    daemon_parser.add_argument("--full", action="store_true")
    daemon_parser.add_argument("--no-workspace", action="store_true")
    daemon_parser.set_defaults(handler=command_daemon)

    serve_parser = commands.add_parser("serve", help="run the MCP stdio server")
    serve_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    serve_parser.set_defaults(handler=command_serve)

    agents_parser = commands.add_parser("agents", help="run four-layer role agents")
    agents_parser.add_argument("project_root", nargs="?", default=".")
    agents_parser.add_argument("question")
    agents_parser.add_argument("--no-llm", action="store_true")
    agents_parser.add_argument("--min-confidence", type=float, default=0.65)
    agents_parser.add_argument("--limit", type=int, default=50)
    agents_parser.set_defaults(handler=command_agents)

    memory_parser = commands.add_parser("memory", help="semantic memory operations")
    memory_sub = memory_parser.add_subparsers(dest="memory_command", required=True)
    memory_generate = memory_sub.add_parser("generate", help="generate semantic memories")
    memory_generate.add_argument("project_root", nargs="?", default=".")
    memory_generate.add_argument("--scope")
    memory_generate.add_argument("--no-llm", action="store_true")
    memory_generate.set_defaults(handler=command_memory)
    memory_review = memory_sub.add_parser("auto-review", help="review stale memories")
    memory_review.add_argument("project_root", nargs="?", default=".")
    memory_review.add_argument("--limit", type=int, default=100)
    memory_review.set_defaults(handler=command_memory)
    memory_lessons = memory_sub.add_parser("lessons", help="extract revert lessons")
    memory_lessons.add_argument("project_root", nargs="?", default=".")
    memory_lessons.add_argument("--limit", type=int, default=20)
    memory_lessons.set_defaults(handler=command_memory)

    architecture_parser = commands.add_parser("architecture", help="contracts and architecture docs")
    architecture_sub = architecture_parser.add_subparsers(dest="architecture_command", required=True)
    for name, extra in (
        ("sync", {}),
        ("contracts", {"limit": 100}),
        ("impact", {"limit": 100, "min_confidence": 0.65}),
        ("docs-index", {}),
        ("docs", {"limit": 100, "kind": None}),
    ):
        command = architecture_sub.add_parser(name)
        command.add_argument("project_root", nargs="?", default=".")
        if "limit" in extra:
            command.add_argument("--limit", type=int, default=extra["limit"])
        if "min_confidence" in extra:
            command.add_argument("--min-confidence", type=float, default=extra["min_confidence"])
        if "kind" in extra:
            command.add_argument("--kind")
        command.set_defaults(handler=command_architecture)

    approvals_parser = commands.add_parser("approvals", help="kernel approvals")
    approvals_sub = approvals_parser.add_subparsers(dest="approvals_command", required=True)
    approvals_list = approvals_sub.add_parser("list")
    approvals_list.add_argument("project_root", nargs="?", default=".")
    approvals_list.add_argument("--status")
    approvals_list.add_argument("--limit", type=int, default=50)
    approvals_list.set_defaults(handler=command_approvals)
    approvals_decide = approvals_sub.add_parser("decide")
    approvals_decide.add_argument("project_root", nargs="?", default=".")
    approvals_decide.add_argument("approval_id", type=int)
    approvals_decide.add_argument("decision", choices=("approved", "rejected"))
    approvals_decide.add_argument("--approver", required=True)
    approvals_decide.add_argument("--reason", required=True)
    approvals_decide.set_defaults(handler=command_approvals)

    patch_parser = commands.add_parser("patch", help="approved patch workflow")
    patch_sub = patch_parser.add_subparsers(dest="patch_command", required=True)
    patch_propose = patch_sub.add_parser("propose")
    patch_propose.add_argument("project_root", nargs="?", default=".")
    patch_propose.add_argument("--file", required=True)
    patch_propose.add_argument("--old-text", required=True)
    patch_propose.add_argument("--new-text", required=True)
    patch_propose.add_argument("--title", required=True)
    patch_propose.add_argument("--created-by", required=True)
    patch_propose.set_defaults(handler=command_patch)
    patch_decide = patch_sub.add_parser("decide")
    patch_decide.add_argument("project_root", nargs="?", default=".")
    patch_decide.add_argument("patch_key")
    patch_decide.add_argument("decision", choices=("approved", "rejected"))
    patch_decide.add_argument("--approver", required=True)
    patch_decide.add_argument("--reason", required=True)
    patch_decide.set_defaults(handler=command_patch)
    patch_apply = patch_sub.add_parser("apply")
    patch_apply.add_argument("project_root", nargs="?", default=".")
    patch_apply.add_argument("patch_key")
    patch_apply.add_argument("--applied-by", required=True)
    patch_apply.add_argument("--test-command")
    patch_apply.set_defaults(handler=command_patch)
    patch_list = patch_sub.add_parser("list")
    patch_list.add_argument("project_root", nargs="?", default=".")
    patch_list.add_argument("--status")
    patch_list.add_argument("--limit", type=int, default=50)
    patch_list.set_defaults(handler=command_patch)
    patch_propose_publish = patch_sub.add_parser("propose-publish")
    patch_propose_publish.add_argument("project_root", nargs="?", default=".")
    patch_propose_publish.add_argument("patch_key")
    patch_propose_publish.add_argument("--remote", default="origin")
    patch_propose_publish.add_argument("--base-branch", default="main")
    patch_propose_publish.add_argument("--branch", required=True)
    patch_propose_publish.add_argument("--title", required=True)
    patch_propose_publish.add_argument("--body")
    patch_propose_publish.add_argument("--requested-by", required=True)
    patch_propose_publish.set_defaults(handler=command_patch)
    patch_publish = patch_sub.add_parser("publish")
    patch_publish.add_argument("project_root", nargs="?", default=".")
    patch_publish.add_argument("patch_key")
    patch_publish.add_argument("--remote", default="origin")
    patch_publish.add_argument("--base-branch", default="main")
    patch_publish.add_argument("--branch", required=True)
    patch_publish.add_argument("--title", required=True)
    patch_publish.add_argument("--body")
    patch_publish.add_argument("--approval-key", required=True)
    patch_publish.add_argument("--published-by", required=True)
    patch_publish.add_argument("--no-push", action="store_true")
    patch_publish.add_argument("--no-pr", action="store_true")
    patch_publish.set_defaults(handler=command_patch)

    evolution_parser = commands.add_parser("evolution", help="offline bounded evolution")
    evolution_sub = evolution_parser.add_subparsers(dest="evolution_command", required=True)
    evolution_distill = evolution_sub.add_parser("distill")
    evolution_distill.add_argument("project_root", nargs="?", default=".")
    evolution_distill.add_argument("--limit", type=int, default=100)
    evolution_distill.set_defaults(handler=command_evolution)
    evolution_create = evolution_sub.add_parser("create")
    evolution_create.add_argument("project_root", nargs="?", default=".")
    evolution_create.add_argument("--object-type", required=True, choices=("router_terms", "strategy_rule", "context_template"))
    evolution_create.add_argument("--name", required=True)
    evolution_create.add_argument("--payload", required=True, help="JSON payload")
    evolution_create.add_argument("--parent-key")
    evolution_create.set_defaults(handler=command_evolution)
    evolution_evaluate = evolution_sub.add_parser("evaluate")
    evolution_evaluate.add_argument("project_root", nargs="?", default=".")
    evolution_evaluate.add_argument("variant_key")
    evolution_evaluate.add_argument("--no-record", action="store_true")
    evolution_evaluate.set_defaults(handler=command_evolution)
    evolution_list = evolution_sub.add_parser("list")
    evolution_list.add_argument("project_root", nargs="?", default=".")
    evolution_list.add_argument("--status")
    evolution_list.add_argument("--limit", type=int, default=50)
    evolution_list.set_defaults(handler=command_evolution)
    evolution_promote = evolution_sub.add_parser("propose-promotion")
    evolution_promote.add_argument("project_root", nargs="?", default=".")
    evolution_promote.add_argument("variant_key")
    evolution_promote.add_argument("--requested-by", required=True)
    evolution_promote.set_defaults(handler=command_evolution)
    evolution_promote = evolution_sub.add_parser("promote")
    evolution_promote.add_argument("project_root", nargs="?", default=".")
    evolution_promote.add_argument("variant_key")
    evolution_promote.add_argument("--approval-key", required=True)
    evolution_promote.add_argument("--promoted-by", required=True)
    evolution_promote.set_defaults(handler=command_evolution)
    evolution_rollback = evolution_sub.add_parser("propose-rollback")
    evolution_rollback.add_argument("project_root", nargs="?", default=".")
    evolution_rollback.add_argument("variant_key")
    evolution_rollback.add_argument("--requested-by", required=True)
    evolution_rollback.add_argument("--reason", required=True)
    evolution_rollback.set_defaults(handler=command_evolution)
    evolution_rollback = evolution_sub.add_parser("rollback")
    evolution_rollback.add_argument("project_root", nargs="?", default=".")
    evolution_rollback.add_argument("variant_key")
    evolution_rollback.add_argument("--approval-key", required=True)
    evolution_rollback.add_argument("--rolled-back-by", required=True)
    evolution_rollback.set_defaults(handler=command_evolution)

    http_parser = commands.add_parser("serve-http", help="run HTTP JSON-RPC server")
    http_parser.add_argument("project_root", nargs="?", default=".")
    http_parser.add_argument("--host", default="127.0.0.1")
    http_parser.add_argument("--port", type=int, default=8787)
    http_parser.set_defaults(handler=command_serve_http)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (ConfigError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("daemon stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

