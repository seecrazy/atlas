import json
import pathlib
import subprocess

from atlas.config import load_config, write_default_config
from atlas.db import connect
from atlas.indexer import index_project
from atlas.store import health, search


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
