import json
import pathlib
import sqlite3
import subprocess

from atlas.config import load_config, write_default_config
from atlas.context import developer_context, module_context, system_context
from atlas.db import SCHEMA_VERSION, connect, utcnow
from atlas.indexer import index_project
from atlas.report import health_report
from atlas.router import route_request
from atlas.store import health, impact, memories, references, review_memory, search
from atlas.store import impact_chain
from atlas.synthesis import converge_findings


def test_go_and_java_basic_index(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "multi-lang"
    root.mkdir()
    (root / "payment.go").write_text(
        """
package payment

import (
  "context"
  "net/http"
)

type Handler struct{}

func (r *Handler) Execute(ctx context.Context) error {
  return nil
}

func ProcessPayment(ctx context.Context) error {
  return nil
}
""",
        encoding="utf-8",
    )
    (root / "UserService.java").write_text(
        """
package payment;

import java.util.Map;

public class UserService {
  public void processPayment() {
  }
}
""",
        encoding="utf-8",
    )
    write_default_config(root, overwrite=True)
    config = load_config(root)
    stats = index_project(config, full=True)

    assert stats["files_changed"] == 2
    assert stats["symbols"] >= 5
    assert stats["imports"] == 3

    conn = connect(config.db_path)
    try:
        go_hits = {item["qualified_name"] for item in search(conn, "Execute")}
        java_hits = {item["qualified_name"] for item in search(conn, "processPayment")}
        assert "payment.Handler.Execute" in go_hits
        assert "payment.UserService.processPayment" in java_hits

        report = health(conn)
        assert report["indexed_languages"]["go"] == 1
        assert report["indexed_languages"]["java"] == 1
    finally:
        conn.close()


def test_rust_basic_index(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "rust-project"
    root.mkdir()
    (root / "service.rs").write_text(
        """
use std::collections::HashMap;

pub struct Server {
    pub name: String,
}

impl Server {
    pub fn ping(&self) -> bool {
        true
    }
}

fn start_server() {}
""",
        encoding="utf-8",
    )
    write_default_config(root, overwrite=True)
    config = load_config(root)
    stats = index_project(config, full=True)
    assert stats["files_changed"] == 1
    conn = connect(config.db_path)
    try:
        names = {item["name"] for item in search(conn, "Server")}
        functions = {item["name"] for item in search(conn, "ping")}
        assert "Server" in names
        assert "ping" in functions
        assert health(conn)["indexed_languages"]["rust"] == 1
    finally:
        conn.close()


def test_reference_resolution_and_cross_repo(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "cross-repo"
    root.mkdir()
    (root / "api").mkdir()
    (root / "client").mkdir()
    (root / "api" / "app.py").write_text(
        "from client.models import User\n\n\ndef use_user():\n    return User\n",
        encoding="utf-8",
    )
    (root / "client" / "models.py").write_text(
        "class User:\n    pass\n",
        encoding="utf-8",
    )
    config_path = root / ".atlas"
    config_path.mkdir()
    config_path.joinpath("config.json").write_text(
        json.dumps(
            {
                "project": "cross-repo",
                "subsystems": [{"name": "default", "repos": ["api", "client"]}],
                "languages": ["python"],
                "exclude": [],
            }
        ),
        encoding="utf-8",
    )
    config = load_config(root)
    index_project(config)
    conn = connect(config.db_path)
    try:
        refs = references(conn, "User")
        assert len(refs) == 1
        assert refs[0]["resolution_status"] == "unique_name"
        assert refs[0]["resolved_symbol"] == "models.User"
        assert refs[0]["source_repo"] == "api"
        assert refs[0]["target_repo"] == "client"
        assert refs[0]["cross_repo"] is True
        assert refs[0]["confidence"] > 0.5

        cross_context = system_context(conn)
        assert cross_context["cross_repo_edges"]
        assert cross_context["cross_repo_edges"][0]["source_repo"] == "api"
        assert cross_context["cross_repo_edges"][0]["target_repo"] == "client"
        assert cross_context["cross_repo_edges"][0]["reference_count"] == 1
    finally:
        conn.close()


def test_impact_confidence_filter(project_root: pathlib.Path) -> None:
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        unfiltered = impact(conn, "RetryClient")
        assert unfiltered["reference_count"] == 1
        assert unfiltered["filtered_count"] == 0

        strict = impact(conn, "RetryClient", min_confidence=0.95)
        assert strict["min_confidence"] == 0.95
        assert strict["reference_count"] == 0
        assert strict["filtered_count"] == 1
        assert strict["references_by_resolution"] == {"unique_name": 1}
        assert references(conn, "RetryClient", min_confidence=0.95) == []
    finally:
        conn.close()


def test_system_module_and_developer_context(project_root: pathlib.Path) -> None:
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        system = system_context(conn)
        assert system["layer"] == "system"
        assert system["repos"][0]["name"] == "demo"
        assert system["repos"][0]["file_count"] == 3
        assert system["cross_repo_edges"] == []
        assert "no resolved cross-repo edges" in system["warnings"][0]

        module = module_context(conn, "demo/client/RetryClient.ts")
        assert module["layer"] == "module"
        assert {item["name"] for item in module["symbols"]} >= {"RetryClient"}
        assert module["inbound_references"][0]["source_file"] == "demo/client/Gateway.ts"
        assert "demo/client/Gateway.ts" in module["related_files"]

        developer = developer_context(
            conn,
            "demo/client/RetryClient.ts",
            symbol="RetryClient",
        )
        assert developer["layer"] == "developer"
        assert developer["target_symbols"][0]["name"] == "RetryClient"
        assert developer["scope_candidates"] == ["demo/client"]
        assert developer["contains_source_text"] is False
        assert "demo/client/Gateway.ts" in developer["related_files"]
    finally:
        conn.close()


def test_impact_chain(project_root: pathlib.Path) -> None:
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        chain = impact_chain(conn, "RetryClient", min_confidence=0.65)
        assert chain["max_hops"] == 2
        assert chain["direct_reference_count"] == 1
        assert chain["first_hop"][0]["source_file"] == "demo/client/Gateway.ts"
        assert chain["second_hop_reference_count"] == 0
    finally:
        conn.close()


def test_impact_chain_second_hop(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "chain"
    root.mkdir()
    (root / "core.py").write_text("class Base:\n    pass\n", encoding="utf-8")
    (root / "mid.py").write_text(
        "from core import Base\n\n\nclass Middle(Base):\n    pass\n",
        encoding="utf-8",
    )
    (root / "top.py").write_text(
        "from mid import Middle\n\n\nclass Top(Middle):\n    pass\n",
        encoding="utf-8",
    )
    write_default_config(root, overwrite=True)
    config = load_config(root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        chain = impact_chain(conn, "Base", min_confidence=0.65)
        assert chain["direct_reference_count"] == 1
        assert chain["first_hop"][0]["source_file"] == "chain/mid.py"
        assert chain["second_hop_reference_count"] == 1
        assert chain["second_hop"][0]["source_file"] == "chain/top.py"
        assert chain["second_hop"][0]["first_hop_file"] == "chain/mid.py"
    finally:
        conn.close()


def test_converge_findings(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "converge"
    root.mkdir()
    (root / "core.py").write_text("class Base:\n    pass\n", encoding="utf-8")
    (root / "mid.py").write_text(
        "from core import Base\n\n\nclass Middle(Base):\n    pass\n",
        encoding="utf-8",
    )
    (root / "top.py").write_text(
        "from mid import Middle\n\n\nclass Top(Middle):\n    pass\n",
        encoding="utf-8",
    )
    write_default_config(root, overwrite=True)
    config = load_config(root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    findings = [
        {
            "role": "architect",
            "claim": "Top depends on Base through Middle",
            "confidence": 0.8,
            "evidence": [{"file": "converge/top.py", "line": 1}],
            "recommended_actions": ["verify contract compatibility"],
        },
        {
            "role": "developer",
            "claim": "Base is defined in core.py",
            "confidence": 0.9,
            "evidence": [{"file": "converge/core.py", "line": 1}],
        },
        {
            "role": "module",
            "claim": "Middle imports Base and is imported by Top",
            "confidence": 0.75,
            "evidence": [{"file": "converge/mid.py", "line": 1}],
        },
        {
            "role": "system",
            "claim": "No separate deployment unit is visible",
            "confidence": 0.7,
            "evidence": [{"file": "converge/top.py", "line": 1}],
        },
    ]
    try:
        result = converge_findings(
            conn,
            "What happens if Base changes?",
            findings,
            impact_symbol="Base",
            min_confidence=0.65,
        )
        assert [item["role"] for item in result["ordered_findings"]] == [
            "developer", "module", "system", "architect"
        ]
        assert result["missing_layers"] == []
        assert result["impact_propagation"]["second_hop_reference_count"] == 1
        assert result["answer_sections"]["implementation_findings"][0]["claim"]
        assert result["evidence_files"] == [
            "converge/core.py", "converge/mid.py", "converge/top.py"
        ]
    finally:
        conn.close()


def test_converge_rejects_unindexed_evidence(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "bad-converge"
    root.mkdir()
    (root / "app.py").write_text("def run():\n    pass\n", encoding="utf-8")
    write_default_config(root, overwrite=True)
    config = load_config(root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    try:
        import pytest

        with pytest.raises(FileNotFoundError):
            converge_findings(
                conn,
                "What changed?",
                [
                    {
                        "role": "developer",
                        "claim": "bad evidence",
                        "evidence": [{"file": "does/not/exist.py", "line": 1}],
                    }
                ],
            )
    finally:
        conn.close()


def test_deterministic_router(project_root: pathlib.Path) -> None:
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        implementation = route_request(
            conn,
            "Read the implementation of execute_with_backoff to find the timeout root cause",
        )
        assert implementation["intent"] == "implementation"
        assert implementation["role"] == "developer"
        assert implementation["scope_candidates"] == ["demo/retry"]
        assert "indexed_symbol_candidates" in implementation["signals"]

        impact_request = route_request(conn, "Which callers reference RetryClient?")
        assert impact_request["intent"] == "impact"
        assert impact_request["role"] == "system"
        assert impact_request["reference_count"] >= 1

        memory_request = route_request(conn, "Show me the revert lesson for retry")
        assert memory_request["intent"] == "memory"
        assert "atlas_memories" in memory_request["recommended_tools"]
    finally:
        conn.close()


def test_memory_retrieval_and_review(project_root: pathlib.Path) -> None:
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        file_id = conn.execute(
            "SELECT id FROM files WHERE path = ?", ("demo/retry/handler.py",)
        ).fetchone()["id"]
        now = utcnow()
        conn.execute(
            """
            INSERT INTO memories(memory_key, layer, claim, source, created_at, updated_at)
            VALUES('mem-test', 'structural', 'RetryHandler owns retry behavior', 'test', ?, ?)
            """,
            (now, now),
        )
        memory_id = conn.execute(
            "SELECT id FROM memories WHERE memory_key = 'mem-test'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO memory_evidence(memory_id, file_id, start_line, end_line) VALUES(?,?,1,3)",
            (memory_id, file_id),
        )
        conn.commit()

        rows = memories(conn, status="valid")
        assert len(rows) == 1
        assert rows[0]["evidence"][0]["file"] == "demo/retry/handler.py"

        reviewed = review_memory(
            conn,
            memory_id,
            "invalid",
            reviewer="human-reviewer",
            reason="claim no longer applies",
        )
        assert reviewed["status"] == "invalid"
        assert not memories(conn, status="stale")
        assert memories(conn, status="invalid")[0]["id"] == memory_id

        revalidated = review_memory(
            conn,
            memory_id,
            "valid",
            reviewer="human-reviewer",
            reason="verified against current implementation",
        )
        assert revalidated["status"] == "valid"
        review_count = conn.execute(
            "SELECT COUNT(*) FROM memory_reviews WHERE memory_id = ?", (memory_id,)
        ).fetchone()[0]
        assert review_count == 2
    finally:
        conn.close()


def test_schema_migration_adds_reference_columns(tmp_path: pathlib.Path) -> None:
    db_path = tmp_path / "legacy.db"
    raw = sqlite3.connect(db_path)
    raw.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    raw.execute("INSERT INTO meta VALUES('schema_version', '1')")
    raw.execute(
        """
        CREATE TABLE refs(
          id INTEGER PRIMARY KEY,
          source_file_id INTEGER NOT NULL,
          source_repo_id INTEGER NOT NULL,
          target_name TEXT NOT NULL,
          target_symbol TEXT,
          line INTEGER NOT NULL,
          kind TEXT NOT NULL DEFAULT 'import'
        )
        """
    )
    raw.execute(
        "INSERT INTO refs(source_file_id, source_repo_id, target_name, line) VALUES(1,1,'demo',1)"
    )
    raw.execute(
        "INSERT INTO refs(source_file_id, source_repo_id, target_name, line) VALUES(1,1,'demo',1)"
    )
    raw.commit()
    raw.close()

    conn = connect(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(refs)")}
        assert {"target_symbol_id", "resolution_status", "resolution_method", "confidence"} <= columns
        assert conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0] == 1
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()["value"]
        assert int(version) == SCHEMA_VERSION
    finally:
        conn.close()


def test_index_diagnostics(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "diagnostics"
    root.mkdir()
    (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    write_default_config(root, overwrite=True)
    config = load_config(root)

    stats = index_project(config, full=True)
    assert stats["diagnostics"] == 1

    conn = connect(config.db_path)
    try:
        row = conn.execute(
            "SELECT level, code, file_path FROM index_diagnostics WHERE file_path = ?",
            ("diagnostics/broken.py",),
        ).fetchone()
        assert row["level"] == "error"
        assert row["code"] == "python_syntax_error"
        assert health(conn)["diagnostics"]["error"] == 1
    finally:
        conn.close()


def _git(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def test_git_incremental_index(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "git-project"
    source_dir = root / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "first.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (source_dir / "second.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    write_default_config(root, overwrite=True)
    (root / ".gitignore").write_text(".atlas/\n", encoding="utf-8")

    _git("init", cwd=root)
    _git("config", "user.name", "Atlas Test", cwd=root)
    _git("config", "user.email", "atlas@example.com", cwd=root)
    _git("add", ".", cwd=root)
    _git("commit", "-m", "initial", cwd=root)

    config = load_config(root)
    first = index_project(config)
    assert first["mode"] == "full"
    conn = connect(config.db_path)
    try:
        state = conn.execute("SELECT last_commit FROM index_state").fetchone()
        assert state is not None and state["last_commit"]
    finally:
        conn.close()

    (source_dir / "second.py").write_text(
        "def second():\n    return 2\n\ndef third():\n    return 3\n",
        encoding="utf-8",
    )
    (source_dir / "first.py").unlink()
    _git("add", ".", cwd=root)
    _git("commit", "-m", "update second and delete first", cwd=root)

    second = index_project(config, include_workspace=False)
    assert second["mode"] == "git-diff-tree"
    assert second["git_candidates"] == 2
    assert second["files_changed"] == 1

    conn = connect(config.db_path)
    try:
        paths = {
            row["path"]
            for row in conn.execute("SELECT path FROM files WHERE repo_id = 1")
        }
        assert "git-project/src/first.py" not in paths
        assert "git-project/src/second.py" in paths
        assert any(item["name"] == "third" for item in search(conn, "third"))
    finally:
        conn.close()


def test_workspace_change_detection(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "workspace-project"
    source_dir = root / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "stable.py").write_text(
        "def stable():\n    return True\n", encoding="utf-8"
    )
    (source_dir / "changed.py").write_text(
        "def changed():\n    return 1\n", encoding="utf-8"
    )
    write_default_config(root, overwrite=True)
    (root / ".gitignore").write_text(".atlas/\n", encoding="utf-8")
    _git("init", cwd=root)
    _git("config", "user.name", "Atlas Test", cwd=root)
    _git("config", "user.email", "atlas@example.com", cwd=root)
    _git("add", ".", cwd=root)
    _git("commit", "-m", "initial", cwd=root)

    config = load_config(root)
    first = index_project(config, full=True)
    assert first["mode"] == "full"
    assert first["files_changed"] == 2

    # An unrelated committed file proves partial candidate scans do not delete
    # unaffected index rows.
    (source_dir / "changed.py").write_text(
        "def changed():\n    return 2\n\n\ndef changed_again():\n    return 3\n",
        encoding="utf-8",
    )
    (source_dir / "added.py").write_text(
        "def added():\n    return True\n", encoding="utf-8"
    )
    second = index_project(config)
    assert second["mode"] == "git-worktree"
    assert second["workspace_candidates"] == 2
    assert second["files_changed"] == 2

    conn = connect(config.db_path)
    try:
        paths = {
            row["path"]
            for row in conn.execute("SELECT path FROM files WHERE repo_id = 1")
        }
        assert "workspace-project/src/stable.py" in paths
        assert "workspace-project/src/changed.py" in paths
        assert "workspace-project/src/added.py" in paths
    finally:
        conn.close()

    # Workspace changes remain candidates because no commit moved HEAD, but the
    # content hashes make the second pass effectively no-op.
    third = index_project(config)
    assert third["mode"] == "git-worktree"
    assert third["files_changed"] == 0
    assert third["files_unchanged"] == 2

    no_workspace = index_project(config, include_workspace=False)
    assert no_workspace["mode"] == "git-diff-tree"
    assert no_workspace["workspace_candidates"] == 0
    assert no_workspace["files_changed"] == 0


def test_health_report_markdown(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "report-project"
    root.mkdir()
    (root / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")
    (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    write_default_config(root, overwrite=True)
    config = load_config(root)
    index_project(config, full=True)

    conn = connect(config.db_path)
    try:
        report = health_report(health(conn))
    finally:
        conn.close()
    assert report.startswith("# ATLAS Health Report")
    assert "`python`: `2`" in report
    assert "Index diagnostics contain errors" not in report
    assert "index diagnostics contain errors" in report
