"""Command line entry points for ATLAS."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .config import ConfigError, load_config, write_default_config
from .db import connect
from .indexer import index_project
from .store import health
from .report import health_report


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

    serve_parser = commands.add_parser("serve", help="run the MCP stdio server")
    serve_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    serve_parser.set_defaults(handler=command_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (ConfigError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

