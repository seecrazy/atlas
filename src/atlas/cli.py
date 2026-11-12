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


def print_json(value: dict | list) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def command_init(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.project_root).expanduser().resolve()
    path = write_default_config(root, overwrite=args.overwrite)
    print(f"Initialized ATLAS config: {path}")
    return 0


def command_index(args: argparse.Namespace) -> int:
    config = load_config(args.project_root)
    stats = index_project(config)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas", description="Layered project knowledge system")
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="initialize an ATLAS project")
    init_parser.add_argument("project_root", nargs="?", default=".", help="target project root")
    init_parser.add_argument("--overwrite", action="store_true", help="overwrite existing config")
    init_parser.set_defaults(handler=command_init)

    index_parser = commands.add_parser("index", help="index configured repositories")
    index_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    index_parser.set_defaults(handler=command_index)

    health_parser = commands.add_parser("health", help="show index health")
    health_parser.add_argument("project_root", nargs="?", default=".", help="ATLAS project root")
    health_parser.set_defaults(handler=command_health)

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

