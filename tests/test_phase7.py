import json
import pathlib

import pytest

from atlas.config import load_config, write_default_config
from atlas.db import connect
from atlas.indexer import index_project
from atlas.mcp import _validate_tool_arguments
from atlas.store import create_scope, read_scoped_file, ScopeError

from atlas.store import references


def test_python_relative_import_resolution(tmp_path):
    root = tmp_path / "resolver"
    root.mkdir()
    (root / "core.py").write_text("class Base:\n    pass\n", encoding="utf-8")
    (root / "worker.py").write_text(
        "from .core import Base\n\n\nclass Worker(Base):\n    pass\n",
        encoding="utf-8",
    )
    write_default_config(root, overwrite=True)
    config = load_config(root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        refs = references(conn, "Base")
        assert refs
        assert refs[0]["resolution_status"] == "resolved"
        assert refs[0]["source_file"] == "resolver/worker.py"
    finally:
        conn.close()


def test_ts_relative_path_resolution(tmp_path):
    root = tmp_path / "ts-resolver"
    root.mkdir()
    (root / "RetryClient.ts").write_text(
        "export class RetryClient {}\n", encoding="utf-8"
    )
    (root / "Gateway.ts").write_text(
        'import { RetryClient } from "./RetryClient";\n\nexport class Gateway {}\n',
        encoding="utf-8",
    )
    write_default_config(root, overwrite=True)
    config = load_config(root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        refs = references(conn, "RetryClient")
        assert refs
        assert refs[0]["resolution_status"] in {"resolved", "ambiguous"}
        assert refs[0]["source_file"] == "ts-resolver/Gateway.ts"
    finally:
        conn.close()


def test_signature_change_detected(tmp_path):
    root = tmp_path / "signature"
    root.mkdir()
    target = root / "api.py"
    target.write_text("def api(value: int) -> int:\n    return value\n", encoding="utf-8")
    write_default_config(root, overwrite=True)
    config = load_config(root)
    index_project(config, full=True)
    target.write_text("def api(value: str) -> str:\n    return value\n", encoding="utf-8")
    stats = index_project(config)
    assert stats["exported_symbol_changes"] == 1
    conn = connect(config.db_path)
    try:
        rows = conn.execute(
            "SELECT change_type FROM exported_symbol_changes WHERE symbol='api'"
        ).fetchall()
        assert "added" in {row["change_type"] for row in rows}
        assert "signature_changed" in {row["change_type"] for row in rows}
    finally:
        conn.close()


def test_call_edges_have_low_confidence(tmp_path):
    root = tmp_path / "calls"
    root.mkdir()
    (root / "app.py").write_text("def run():\n    helper()\n", encoding="utf-8")
    write_default_config(root, overwrite=True)
    config = load_config(root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        row = conn.execute(
            "SELECT confidence, resolution_status FROM refs WHERE kind='call'"
        ).fetchone()
        assert row["confidence"] == pytest.approx(0.45)
        assert row["resolution_status"] == "unresolved"
    finally:
        conn.close()


def test_scope_read_blocks_symlink_escape(tmp_path):
    root = tmp_path / "scoped"
    root.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("secret", encoding="utf-8")
    target = root / "linked.py"
    target.symlink_to(secret)
    write_default_config(root, overwrite=True)
    config = load_config(root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        row = conn.execute("SELECT path FROM files WHERE path LIKE '%linked.py'").fetchone()
        assert row is None
        scope = create_scope(conn, config, ["scoped"])
        with pytest.raises(FileNotFoundError):
            read_scoped_file(conn, scope["token"], "scoped/linked.py")
    finally:
        conn.close()


def test_patch_test_command_allowlist(tmp_path):
    root = tmp_path / "patch-allow"
    root.mkdir()
    (root / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    write_default_config(root, overwrite=True)
    config_path = root / ".atlas" / "config.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["allowed_test_commands"] = ["python3 -m pytest"]
    config_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    config = load_config(root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        from atlas.changes import apply_patch, decide_patch, propose_patch

        proposed = propose_patch(
            conn,
            config,
            "patch-allow/app.py",
            old_text="def run():",
            new_text="def run(value=1):",
            title="change run",
            created_by="agent",
        )
        conn.execute(
            "UPDATE approvals SET status='approved', approver='human', reason='ok', decided_at=datetime('now') WHERE approval_key=?",
            (proposed["approval"]["approval_key"],),
        )
        conn.execute("UPDATE patches SET status='approved' WHERE patch_key=?", (proposed["patch_key"],))
        with pytest.raises(ValueError, match="allowed_test_commands"):
            apply_patch(conn, config, proposed["patch_key"], applied_by="human", test_command="true")
    finally:
        conn.close()


def test_mcp_argument_validation():
    with pytest.raises(ValueError, match="missing required argument"):
        _validate_tool_arguments("atlas_search", {})
    with pytest.raises(ValueError, match="unknown arguments"):
        _validate_tool_arguments("atlas_search", {"query": "x", "bogus": True})
    with pytest.raises(ValueError, match="below minimum"):
        _validate_tool_arguments("atlas_search", {"query": "x", "limit": 0})
