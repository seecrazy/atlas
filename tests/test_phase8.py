import json
import pathlib

import pytest

from atlas.config import load_config, write_default_config
from atlas.daemon import run_daemon
from atlas.db import connect, SCHEMA_VERSION
from atlas.evolution import create_variant, shadow_evaluate, decay_strategy_utility
from atlas.operations import acquire_daemon_lock, release_daemon_lock, prune_audit_events, doctor
from atlas.pipeline import run_pipeline
from atlas.store import health


def test_daemon_lock_blocks_second_daemon(tmp_path):
    root = tmp_path / "lock-project"
    root.mkdir()
    (root / "app.py").write_text("def run():\n    return True\n", encoding="utf-8")
    write_default_config(root, overwrite=True)
    config = load_config(root)
    token = acquire_daemon_lock(config)
    assert token
    with pytest.raises(RuntimeError, match="another ATLAS daemon"):
        acquire_daemon_lock(config)
    assert release_daemon_lock(config, token)
    assert not (root / ".atlas" / "daemon.lock").exists()


def test_daemon_backoff_and_metrics(project_root):
    config = load_config(project_root)
    cycles = []
    result = run_daemon(config, interval=0, max_iterations=1, on_cycle=cycles.append)
    assert result["iteration_count"] == 1
    assert cycles[0]["trace_id"]
    conn = connect(config.db_path)
    try:
        metrics = conn.execute(
            "SELECT trace_id, event_type, status, duration_ms FROM run_metrics WHERE event_type='daemon.cycle'"
        ).fetchall()
        assert len(metrics) == 1
        assert metrics[0]["status"] == "completed"
    finally:
        conn.close()


def test_pipeline_trace_budget_and_metrics(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        result = run_pipeline(conn, config, "Which callers reference RetryClient?")
        assert result["trace_id"]
        assert result["budget"]["calls"] >= 1
        metrics = conn.execute(
            "SELECT trace_id, event_type, status FROM run_metrics WHERE trace_id=?",
            (result["trace_id"],),
        ).fetchall()
        assert len(metrics) == 1
    finally:
        conn.close()


def test_mcp_metrics(project_root):
    from atlas.mcp import McpServer

    config = load_config(project_root)
    server = McpServer(config)
    try:
        response = server.handle({
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "atlas_search", "arguments": {"query": "RetryClient"}},
        })
        assert response["result"]["isError"] is False
        metrics = server.conn.execute(
            "SELECT event_type, status FROM run_metrics WHERE event_type='mcp.atlas_search'"
        ).fetchall()
        assert len(metrics) == 1
    finally:
        server.close()


def test_mcp_budget_exceeded(project_root):
    from atlas.mcp import McpServer

    config = load_config(project_root)
    raw_config = json.loads((config.project_root / ".atlas" / "config.json").read_text(encoding="utf-8"))
    raw_config["budgets"]["max_calls"] = 1
    (config.project_root / ".atlas" / "config.json").write_text(
        json.dumps(raw_config, indent=2), encoding="utf-8"
    )
    config = load_config(config.project_root)
    server = McpServer(config)
    try:
        first = server.handle({
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "atlas_search", "arguments": {"query": "x"}},
        })
        assert first["result"]["isError"] is False
        response = server.handle({
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/call",
            "params": {"name": "atlas_search", "arguments": {"query": "x"}},
        })
        assert response["result"]["isError"] is True
        assert "budget exceeded" in response["result"]["content"][0]["text"]
    finally:
        server.close()


def test_shadow_and_decay(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        variant = create_variant(
            conn,
            object_type="strategy_rule",
            name="retry impact shadow",
            payload={"pattern": "retry", "intent": "impact", "role": "system", "utility_score": 0.8},
        )
        result = shadow_evaluate(conn, config, variant["variant_key"])
        assert result["trace_id"]
        assert result["baseline_score"] is not None
        assert result["variant_score"] is not None

        conn.execute(
            """
            INSERT INTO strategy_rules(
              rule_key, pattern, intent, role, utility_score, hits,
              source, created_at, updated_at
            ) VALUES('stale-rule', 'x', 'impact', 'system', 0.9, 0,
                     'audit-distiller', datetime('now', '-40 days'), datetime('now', '-40 days'))
            """
        )
        decayed = decay_strategy_utility(conn, decay_days=30, decay_rate=0.5)
        assert decayed["count"] == 1
    finally:
        conn.close()


def test_maintenance_and_doctor(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        result = prune_audit_events(conn, retention_days=90, max_events=10)
        assert result["remaining"] >= 0
    finally:
        conn.close()

    report = doctor(config)
    assert report["ok"]
    assert report["schema_version"] == SCHEMA_VERSION
