import json
import pathlib

from atlas.config import load_config, write_default_config
from atlas.context import developer_context, module_context, system_context
from atlas.daemon import run_daemon
from atlas.db import connect
from atlas.indexer import index_project
from atlas.pipeline import run_pipeline
from atlas.propagation import export_propagation
from atlas.router import evaluate_router
from atlas.store import health


def test_frozen_router_validation(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        metrics = evaluate_router(conn)
        assert metrics["case_count"] == 20
        assert metrics["intent_correct"] == 20
        assert metrics["intent_accuracy"] == 1.0
        assert metrics["role_evaluated"] > 0
        assert metrics["role_correct"] == metrics["role_evaluated"]

        validation = health(conn)["router_validation"]
        assert validation["case_count"] == 20
        assert validation["intent_accuracy"] == 1.0
    finally:
        conn.close()


def test_export_propagation_added_removed(tmp_path: pathlib.Path):
    root = tmp_path / "propagation-project"
    source_dir = root / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "api.py").write_text(
        "def old_api():\n    return 1\n", encoding="utf-8"
    )
    write_default_config(root, overwrite=True)
    (root / ".gitignore").write_text(".atlas/\n", encoding="utf-8")

    import subprocess

    def git(*args):
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)

    git("init")
    git("config", "user.name", "Atlas Test")
    git("config", "user.email", "atlas@example.com")
    git("add", ".")
    git("commit", "-m", "initial")
    config = load_config(root)
    index_project(config)

    (source_dir / "api.py").write_text(
        "def new_api():\n    return 2\n", encoding="utf-8"
    )
    stats = index_project(config)
    assert stats["exported_symbol_changes"] == 2

    conn = connect(config.db_path)
    try:
        report = export_propagation(conn, min_confidence=0.65)
        assert report["symbol_change_count"] == 3
        assert {item["change_type"] for item in report["changes"]} == {"added", "removed"}
        assert {item["symbol"] for item in report["changes"]} == {"old_api", "new_api"}
    finally:
        conn.close()


def test_pipeline_route_context_worker_convergence(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        result = run_pipeline(
            conn,
            config,
            "Which callers reference RetryClient?",
            min_confidence=0.65,
        )
        assert result["route"]["intent"] == "impact"
        assert {"system", "module", "impact"} <= set(result["contexts"])
        roles = {item["role"] for item in result["workers"]["findings"]}
        assert {"developer", "module", "system", "architect"} <= roles
        assert result["convergence"] is not None
        assert result["convergence"]["role_summary"]["architect"] >= 1
        assert result["convergence"]["impact_propagation"]["first_hop"]
    finally:
        conn.close()


def test_pipeline_warns_without_findings(tmp_path):
    root = tmp_path / "empty-project"
    root.mkdir()
    write_default_config(root, overwrite=True)
    config = load_config(root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        result = run_pipeline(conn, config, "What is the architecture contract for payment?")
        assert result["route"]["intent"] == "locate"
        assert "no structural worker findings; pipeline cannot converge" in result["warnings"]
        assert result["convergence"] is None
    finally:
        conn.close()


def test_daemon_runs_multiple_cycles(project_root):
    config = load_config(project_root)
    cycles = []
    result = run_daemon(
        config,
        interval=0,
        max_iterations=2,
        on_cycle=cycles.append,
    )
    assert result["iteration_count"] == 2
    assert len(cycles) == 2
    assert cycles[0]["stats"]["mode"] == "full"
    assert cycles[1]["stats"]["mode"] == "full"
    assert all(cycle["error"] is None for cycle in cycles)
