import json
import pathlib
import sqlite3
import subprocess

from atlas.config import load_config, write_default_config
from atlas.db import SCHEMA_VERSION, connect
from atlas.indexer import index_project
from atlas.store import health, references, search


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

    second = index_project(config)
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
