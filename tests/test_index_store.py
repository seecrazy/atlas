import pytest

from atlas.config import load_config
from atlas.db import connect
from atlas.db import utcnow
from atlas.indexer import index_project
from atlas.store import (
    ScopeError,
    create_scope,
    file_context,
    health,
    impact,
    references,
    scoped_search,
    search,
    symbols,
)


def test_python_and_ts_index(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        h = health(conn)
        assert h["repo_count"] == 1
        assert h["file_count"] == 3
        assert h["symbol_count"] >= 6
        assert h["indexed_languages"]["python"] == 1
        assert h["indexed_languages"]["typescript"] == 2

        py_hits = search(conn, "execute_with_backoff")
        assert len(py_hits) == 1
        assert py_hits[0]["file"] == "demo/retry/handler.py"
        assert py_hits[0]["line"] >= 8

        ts_hits = search(conn, "RetryClient")
        assert {item["file"] for item in ts_hits} == {"demo/client/RetryClient.ts"}
    finally:
        conn.close()


def test_references_and_impact(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        refs = references(conn, "RetryClient")
        assert len(refs) == 1
        assert refs[0]["source_file"] == "demo/client/Gateway.ts"

        result = impact(conn, "RetryClient")
        assert result["reference_count"] == 1
        assert result["affected_repos"] == ["demo"]
    finally:
        conn.close()


def test_symbols_and_file_context(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        rows = symbols(conn, query="RetryHandler")
        assert rows
        assert rows[0]["kind"] == "class"
        context = file_context(conn, "demo/retry/handler.py")
        assert context["module"] == "retry.handler"
        assert {item["name"] for item in context["symbols"]} >= {
            "RetryHandler",
            "execute_with_backoff",
        }
        assert context["imports"][0]["target"] == "config.retry.load_retry_config"
    finally:
        conn.close()


def test_scope_filters_and_expires(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        scope = create_scope(conn, config, ["demo/retry"])
        results = scoped_search(conn, scope["token"], "Retry")
        assert results
        assert {item["file"] for item in results} == {"demo/retry/handler.py"}

        row = conn.execute(
            "SELECT status FROM scope_tokens WHERE token = ?", (scope["token"],)
        ).fetchone()
        assert row["status"] == "active"

        conn.execute(
            "UPDATE scope_tokens SET status = 'expired' WHERE token = ?",
            (scope["token"],),
        )
        conn.commit()
        with pytest.raises(ScopeError):
            scoped_search(conn, scope["token"], "Retry")
    finally:
        conn.close()


def test_memory_drift_marks_evidence_stale(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        file_row = conn.execute(
            "SELECT id FROM files WHERE path = ?", ("demo/retry/handler.py",)
        ).fetchone()
        now = utcnow()
        conn.execute(
            """
            INSERT INTO memories(memory_key, layer, claim, source, created_at, updated_at)
            VALUES('mem-retry-001', 'structural', 'RetryHandler is in retry/handler.py', 'test', ?, ?)
            """,
            (now, now),
        )
        memory_id = conn.execute(
            "SELECT id FROM memories WHERE memory_key = ?", ("mem-retry-001",)
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO memory_evidence(memory_id, file_id, start_line, end_line) VALUES(?,?,1,2)",
            (memory_id, file_row["id"]),
        )
        conn.commit()

        target = project_root / "retry" / "handler.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
        stats = index_project(config)
        assert stats["memories_marked_stale"] == 1
        status = conn.execute(
            "SELECT status FROM memories WHERE memory_key = ?", ("mem-retry-001",)
        ).fetchone()["status"]
        assert status == "stale"
    finally:
        conn.close()


def test_incremental_index_skips_unchanged_files(project_root):
    config = load_config(project_root)
    first = index_project(config)
    assert first["files_unchanged"] == 3
    assert first["files_changed"] == 0

    target = project_root / "retry" / "handler.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
    second = index_project(config)
    assert second["files_changed"] == 1
    assert second["files_unchanged"] == 2

