import json
import pathlib

import pytest

from atlas.agents import run_layered_agents
from atlas.architecture import contract_impact, ingest_architecture_docs, list_architecture_docs, list_contracts, sync_contracts
from atlas.changes import apply_patch, decide_patch, propose_patch
from atlas.config import load_config, write_default_config
from atlas.db import connect
from atlas.evolution import create_variant, evaluate_variant, list_variants
from atlas.indexer import index_project
from atlas.kernel import request_approval
from atlas.memory import auto_review_stale_memories, generate_semantic_memories
from atlas.store import health


def test_call_edges_are_indexed(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        calls = conn.execute(
            "SELECT COUNT(*) FROM refs WHERE kind = 'call'"
        ).fetchone()[0]
        assert calls > 0
    finally:
        conn.close()


def test_semantic_memory_and_auto_review(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        result = generate_semantic_memories(conn, config, use_llm=False)
        assert result["count"] > 0
        h = health(conn)
        assert h["semantic_memory_count"] > 0
        assert h["memory_by_layer"]["semantic"] == h["semantic_memory_count"]

        conn.execute("UPDATE memories SET status = 'stale' WHERE layer = 'semantic'")
        conn.commit()
        reviewed = auto_review_stale_memories(conn)
        assert reviewed["count"] > 0
        assert all(item["status"] in {"valid", "invalid"} for item in reviewed["reviewed"])
    finally:
        conn.close()


def test_contracts_docs_and_impact(tmp_path):
    root = tmp_path / "contract-project"
    root.mkdir()
    (root / "api.py").write_text("def create_order():\n    return True\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "ADR-001-order.md").write_text(
        "# ADR 001: Order API\n\nOrder.create is a stable contract.\n", encoding="utf-8"
    )
    write_default_config(root, overwrite=True)
    config_path = root / ".atlas" / "config.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["contracts"] = [{
        "name": "create_order",
        "provider_repo": "contract-project",
        "version": "v1",
        "consumers": ["billing"],
    }]
    config_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    config = load_config(root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        synced = sync_contracts(conn, config)
        assert synced["count"] == 1
        assert len(list_contracts(conn)) == 1
        impact = contract_impact(conn, "create_order")
        assert impact["reference_count"] == 0

        docs = ingest_architecture_docs(conn, config)
        assert docs["count"] == 1
        assert docs["indexed"][0]["kind"] == "adr"
        assert len(list_architecture_docs(conn, kind="adr")) == 1
        assert health(conn)["architecture_doc_count"] == 1
    finally:
        conn.close()


def test_layered_agents_use_scope_and_kernel(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        result = run_layered_agents(
            conn,
            config,
            "Read the implementation of execute_with_backoff",
            use_llm=False,
        )
        assert result["run_key"]
        assert result["route"]["intent"] == "implementation"
        assert set(result["contexts"]) >= {"architect", "system", "module", "developer"}
        assert result["contexts"]["developer_source"]["file"]
        roles = {item["role"] for item in result["workers"]["findings"]}
        assert roles == {"architect", "system", "module", "developer"}
        assert result["convergence"] is not None
        assert result["private_score"] > 0
        assert health(conn)["agent_run_count"] >= 1
    finally:
        conn.close()


def test_approved_patch_workflow(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        target = project_root / "retry" / "handler.py"
        old_text = "def execute_with_backoff(task):"
        new_text = "def execute_with_backoff(task, max_attempts=None):"
        proposed = propose_patch(
            conn,
            config,
            "demo/retry/handler.py",
            old_text=old_text,
            new_text=new_text,
            title="add retry cap parameter",
            created_by="agent",
        )
        assert proposed["approval"]["status"] == "pending"
        assert "def execute_with_backoff" in proposed["diff"]

        with pytest.raises(ValueError):
            apply_patch(conn, config, proposed["patch_key"], applied_by="human")

        decided = decide_patch(
            conn,
            proposed["patch_key"],
            "approved",
            approver="human",
            reason="approved structural change",
        )
        assert decided["status"] == "approved"
        applied = apply_patch(
            conn,
            config,
            proposed["patch_key"],
            applied_by="human",

        )
        assert applied["status"] == "applied"
        assert new_text in target.read_text(encoding="utf-8")
    finally:
        conn.close()


def test_evolution_is_offline_and_scored(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        variant = create_variant(
            conn,
            object_type="router_terms",
            name="add timeout terms",
            payload={"add_terms": ["timeout", "latency"]},
        )
        result = evaluate_variant(conn, config, variant["variant_key"], record=True)
        assert result["status"] in {"accepted", "rejected"}
        assert result["score"] >= 0.8
        assert len(list_variants(conn, status=result["status"])) >= 1
        assert health(conn)["accepted_variant_count"] >= 1
    finally:
        conn.close()


def test_kernel_high_risk_requires_human(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        approval = request_approval(
            conn,
            "delete_file",
            {"file": "demo/retry/handler.py"},
            requested_by="agent",
        )
        assert approval["risk"] == "high"
        assert approval["status"] == "pending"
        assert health(conn)["pending_approval_count"] >= 1
    finally:
        conn.close()
