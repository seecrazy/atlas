"""SQLite schema and connection helpers."""

from __future__ import annotations

import json
import pathlib
import sqlite3
from datetime import datetime, timezone


SCHEMA_VERSION = 4

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repos (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  subsystem TEXT NOT NULL,
  path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  language TEXT NOT NULL,
  module_name TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  mtime REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'valid',
  UNIQUE(repo_id, path)
);

CREATE TABLE IF NOT EXISTS symbols (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  qualified_name TEXT NOT NULL,
  kind TEXT NOT NULL,
  parent_qualified TEXT,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  exported INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS refs (
  id INTEGER PRIMARY KEY,
  source_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  source_repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  target_name TEXT NOT NULL,
  target_symbol TEXT,
  target_symbol_id INTEGER,
  resolution_status TEXT NOT NULL DEFAULT 'unresolved',
  resolution_method TEXT,
  confidence REAL NOT NULL DEFAULT 0.0,
  line INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'import'
);

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY,
  memory_key TEXT NOT NULL UNIQUE,
  layer TEXT NOT NULL,
  claim TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.5,
  utility_score REAL NOT NULL DEFAULT 0.5,
  status TEXT NOT NULL DEFAULT 'valid',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_evidence (
  memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  start_line INTEGER NOT NULL DEFAULT 1,
  end_line INTEGER NOT NULL,
  PRIMARY KEY(memory_id, file_id)
);

CREATE TABLE IF NOT EXISTS memory_reviews (
  id INTEGER PRIMARY KEY,
  memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  reviewer TEXT NOT NULL,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exported_symbol_changes (
  id INTEGER PRIMARY KEY,
  repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  file_path TEXT NOT NULL,
  symbol TEXT NOT NULL,
  qualified_name TEXT NOT NULL,
  change_type TEXT NOT NULL,
  indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scope_tokens (
  id INTEGER PRIMARY KEY,
  token TEXT NOT NULL UNIQUE,
  paths_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_by TEXT NOT NULL DEFAULT 'atlas-cli'
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'atlas',
  payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_repo_path ON files(repo_id, path);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_references_target ON refs(target_name);
CREATE INDEX IF NOT EXISTS idx_references_source ON refs(source_file_id);
CREATE INDEX IF NOT EXISTS idx_references_target_symbol ON refs(target_symbol_id);

CREATE TABLE IF NOT EXISTS index_state (
  repo_id INTEGER PRIMARY KEY REFERENCES repos(id) ON DELETE CASCADE,
  last_commit TEXT NOT NULL,
  last_indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS index_diagnostics (
  id INTEGER PRIMARY KEY,
  repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  file_path TEXT NOT NULL,
  level TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_file ON memory_evidence(file_id);
CREATE INDEX IF NOT EXISTS idx_memory_reviews_memory ON memory_reviews(memory_id);
CREATE INDEX IF NOT EXISTS idx_export_changes_repo ON exported_symbol_changes(repo_id, file_path);
CREATE INDEX IF NOT EXISTS idx_export_changes_type ON exported_symbol_changes(change_type, indexed_at);
CREATE INDEX IF NOT EXISTS idx_scope_token ON scope_tokens(token);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_diagnostics_repo ON index_diagnostics(repo_id, file_path);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exported_symbol_changes (
          id INTEGER PRIMARY KEY,
          repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
          file_path TEXT NOT NULL,
          symbol TEXT NOT NULL,
          qualified_name TEXT NOT NULL,
          change_type TEXT NOT NULL,
          indexed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_export_changes_repo "
        "ON exported_symbol_changes(repo_id, file_path)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_export_changes_type "
        "ON exported_symbol_changes(change_type, indexed_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_reviews (
          id INTEGER PRIMARY KEY,
          memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
          reviewer TEXT NOT NULL,
          decision TEXT NOT NULL,
          reason TEXT NOT NULL,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_reviews_memory ON memory_reviews(memory_id)"
    )
    if "refs" not in {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='refs'"
        )
    }:
        return
    columns = _table_columns(conn, "refs")
    statements = []
    if "target_symbol_id" not in columns:
        statements.append("ALTER TABLE refs ADD COLUMN target_symbol_id INTEGER")
    if "resolution_status" not in columns:
        statements.append(
            "ALTER TABLE refs ADD COLUMN resolution_status TEXT NOT NULL DEFAULT 'unresolved'"
        )
    if "resolution_method" not in columns:
        statements.append("ALTER TABLE refs ADD COLUMN resolution_method TEXT")
    if "confidence" not in columns:
        statements.append("ALTER TABLE refs ADD COLUMN confidence REAL NOT NULL DEFAULT 0.0")
    for statement in statements:
        conn.execute(statement)
    conn.execute(
        """
        DELETE FROM refs
         WHERE id NOT IN (
           SELECT MIN(id) FROM refs
           GROUP BY source_file_id, target_name, line, kind
         )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_refs_unique_edge "
        "ON refs(source_file_id, target_name, line, kind)"
    )
    if "meta" in {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        )
    }:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )


def connect(db_path: str | pathlib.Path, *, create: bool = False) -> sqlite3.Connection:
    path = pathlib.Path(db_path)
    if not create and not path.is_file():
        raise FileNotFoundError(f"ATLAS database not found: {path}. Run `atlas index` first.")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    if create:
        conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def audit(
    conn: sqlite3.Connection,
    event_type: str,
    payload: dict,
    *,
    actor: str = "atlas",
) -> None:
    conn.execute(
        "INSERT INTO audit_events(created_at, event_type, actor, payload_json) VALUES(?,?,?,?)",
        (utcnow(), event_type, actor, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )

