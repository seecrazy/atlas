import json
import pathlib

import pytest

from atlas.changes import propose_publication, publish_patch
from atlas.config import load_config, write_default_config
from atlas.db import connect
from atlas.evaluation import run_production_evaluation
from atlas.evolution import create_variant, evaluate_variant, propose_promotion, promote_variant, propose_rollback, rollback_variant
from atlas.indexer import index_project
from atlas.store import health, hybrid_search, rebuild_search_index
from atlas.tree_backend import tree_sitter_available


def test_tree_sitter_backend(project_root):
    assert tree_sitter_available()
    config = load_config(project_root)
    assert config.parser_backend == "auto"
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        hits = {row["qualified_name"] for row in conn.execute(
            "SELECT qualified_name FROM symbols WHERE name = 'RetryClient'"
        )}
        assert "client.RetryClient.RetryClient" in hits
    finally:
        conn.close()


def test_hybrid_search(project_root):
    config = load_config(project_root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        results = hybrid_search(conn, "RetryClient")
        assert results
        assert any(item["doc_type"] == "symbol" and item["path"].endswith("RetryClient.ts") for item in results)
        assert all(item["score"] >= 0 for item in results)
        assert rebuild_search_index(conn)["indexed"] > 0
    finally:
        conn.close()


def test_production_evaluation(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        result = run_production_evaluation(conn, config, use_llm=False)
        assert result["router"]["intent_accuracy"] == 1.0
        assert result["agents"]["case_count"] >= 1
        assert result["agents"]["evidence_coverage"] >= 0
        assert health(conn)["agent_run_count"] >= 1
    finally:
        conn.close()


def test_variant_promotion_rollback(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        variant = create_variant(
            conn,
            object_type="strategy_rule",
            name="timeout impact rule",
            payload={
                "pattern": "timeout",
                "intent": "impact",
                "role": "system",
                "utility_score": 0.85,
            },
        )
        evaluated = evaluate_variant(conn, config, variant["variant_key"])
        assert evaluated["status"] == "accepted"

        promotion = propose_promotion(conn, variant["variant_key"], requested_by="agent")
        assert promotion["risk"] == "high"
        conn.execute(
            "UPDATE approvals SET status='approved', approver='human', reason='reviewed', decided_at=datetime('now') WHERE approval_key=?",
            (promotion["approval_key"],),
        )
        promoted = promote_variant(
            conn,
            variant["variant_key"],
            approval_key=promotion["approval_key"],
            promoted_by="human",
        )
        assert promoted["status"] == "promoted"
        assert conn.execute(
            "SELECT COUNT(*) FROM strategy_rules WHERE source=?",
            (variant["variant_key"],),
        ).fetchone()[0] == 1

        rollback = propose_rollback(
            conn,
            variant["variant_key"],
            requested_by="human",
            reason="bad effect",
        )
        conn.execute(
            "UPDATE approvals SET status='approved', approver='human', reason='reviewed', decided_at=datetime('now') WHERE approval_key=?",
            (rollback["approval_key"],),
        )
        rolled = rollback_variant(
            conn,
            variant["variant_key"],
            approval_key=rollback["approval_key"],
            rolled_back_by="human",
        )
        assert rolled["status"] == "rolled_back"
        assert conn.execute(
            "SELECT COUNT(*) FROM strategy_rules WHERE source=?",
            (variant["variant_key"],),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_patch_publication_requires_approved_high_risk(project_root, monkeypatch):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        conn.execute(
            """
            INSERT INTO patches(
              patch_key, title, file_path, old_text, new_text, diff_text,
              status, created_by, created_at
            ) VALUES('test-patch', 'test', 'demo/retry/handler.py', 'a', 'b', 'diff', 'applied', 'test', datetime('now'))
            """
        )
        proposal = propose_publication(
            conn,
            config,
            "test-patch",
            remote="origin",
            base_branch="main",
            branch="atlas/test",
            title="test",
            requested_by="agent",
        )
        assert proposal["status"] == "pending"
        assert health(conn)["pending_approval_count"] >= 1

        from atlas import changes

        monkeypatch.setattr(changes, "_git", lambda repo_path, args, timeout=60.0: "atlas/test")
        monkeypatch.setattr(changes, "_gh", lambda repo_path, args, timeout=60.0: "https://example.com/pr/1")
        conn.execute(
            "UPDATE approvals SET status='approved', approver='human', reason='reviewed', decided_at=datetime('now') WHERE approval_key=?",
            (proposal["approval_key"],),
        )
        published = publish_patch(
            conn,
            config,
            "test-patch",
            remote="origin",
            base_branch="main",
            branch="atlas/test",
            title="test",
            approval_key=proposal["approval_key"],
            published_by="human",
            push=False,
            create_pr=True,
        )
        assert published["pr_url"] == "https://example.com/pr/1"
    finally:
        conn.close()
