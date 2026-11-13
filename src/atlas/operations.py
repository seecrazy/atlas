"""Operational runtime primitives: tracing, metrics, locks, retention, doctor."""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from .db import SCHEMA_VERSION, audit, connect, utcnow


def new_trace_id() -> str:
    return uuid.uuid4().hex


def record_metric(
    conn: sqlite3.Connection,
    trace_id: str,
    event_type: str,
    *,
    actor: str = "atlas",
    status: str = "completed",
    duration_ms: float = 0.0,
    result_count: int = 0,
    payload: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO run_metrics(
          trace_id, event_type, actor, status, duration_ms,
          result_count, payload_json, created_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            trace_id,
            event_type,
            actor,
            status,
            round(max(0.0, float(duration_ms)), 3),
            int(result_count),
            json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
            utcnow(),
        ),
    )


def trace_operation(conn: sqlite3.Connection, trace_id: str, event_type: str):
    """Return a context manager factory that records duration/status/count."""

    @contextmanager
    def operation(*, actor: str = "atlas", payload: dict | None = None) -> Iterator[dict]:
        started = time.perf_counter()
        state = {"status": "completed", "result_count": 0, "error": None}
        try:
            yield state
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = str(exc)
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            metric_payload = dict(payload or {})
            if state.get("error"):
                metric_payload["error"] = state["error"]
            record_metric(
                conn,
                trace_id,
                event_type,
                actor=actor,
                status=state["status"],
                duration_ms=duration_ms,
                result_count=int(state["result_count"]),
                payload=metric_payload,
            )
            audit(
                conn,
                f"{event_type}.{state['status']}",
                {"trace_id": trace_id, "result_count": state["result_count"], **metric_payload},
                actor=actor,
            )
            conn.commit()

    return operation


def _lock_path(config) -> Path:
    return config.atlas_dir / "daemon.lock"


def acquire_daemon_lock(config, *, stale_seconds: float = 60.0) -> str:
    config.atlas_dir.mkdir(parents=True, exist_ok=True)
    path = _lock_path(config)
    now = time.time()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            pid = int(raw.get("pid", 0))
            acquired_at = float(raw.get("epoch", 0))
            alive = False
            if pid == os.getpid():
                alive = True
            elif hasattr(os, "kill"):
                os.kill(pid, 0)
                alive = True
            if alive and now - acquired_at < stale_seconds:
                raise RuntimeError(f"another ATLAS daemon holds lock: pid={pid}")
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        path.unlink()
    token = uuid.uuid4().hex
    payload = {
        "pid": os.getpid(),
        "token": token,
        "host": socket.gethostname(),
        "epoch": now,
        "acquired_at": utcnow(),
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
    finally:
        os.close(fd)
    return token


def heartbeat_daemon_lock(config, token: str) -> bool:
    path = _lock_path(config)
    if not path.is_file():
        return False
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("token") != token:
        return False
    raw["heartbeat_at"] = utcnow()
    raw["epoch"] = time.time()
    path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    return True


def release_daemon_lock(config, token: str) -> bool:
    path = _lock_path(config)
    if not path.is_file():
        return False
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("token") != token:
        return False
    path.unlink()
    return True


def prune_audit_events(
    conn: sqlite3.Connection,
    *,
    retention_days: int = 90,
    max_events: int = 200_000,
) -> dict:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))
    ).isoformat()
    cursor = conn.execute("DELETE FROM audit_events WHERE created_at < ?", (cutoff,))
    deleted_by_age = int(cursor.rowcount or 0)
    deleted_by_limit = 0
    total = int(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
    if total > max_events:
        keep_id = conn.execute(
            "SELECT id FROM audit_events ORDER BY id DESC LIMIT 1 OFFSET ?",
            (max_events,),
        ).fetchone()
        if keep_id is not None:
            cursor = conn.execute("DELETE FROM audit_events WHERE id <= ?", (int(keep_id["id"]),))
            deleted_by_limit = int(cursor.rowcount or 0)
    conn.execute("DELETE FROM run_metrics WHERE created_at < ?", (cutoff,))
    conn.commit()
    return {
        "deleted_by_age": deleted_by_age,
        "deleted_by_limit": deleted_by_limit,
        "remaining": int(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]),
    }


def doctor(config) -> dict:
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "status": "ok" if ok else "error", "detail": detail})

    version = platform.python_version()
    check("python", tuple(map(int, version.split("."))) >= (3, 9), f"Python {version}")
    try:
        conn = connect(config.db_path)
        conn.execute("SELECT COUNT(*) FROM search_index").fetchone()
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        schema_version = int(row["value"]) if row else 0
        conn.close()
        check("database", True, f"schema_version={schema_version}")
        check("fts5", True, "SQLite FTS5 available")
    except sqlite3.OperationalError as exc:
        detail = str(exc)
        check("database", False, detail)
        check("fts5", False, "FTS5 unavailable" if "fts5" in detail.lower() else "database unavailable")
    except Exception as exc:
        check("database", False, str(exc))
        check("fts5", False, "database unavailable")

    for command, label in (("git", "git"), ("gh", "github_cli")):
        found = shutil.which(command) is not None
        check(label, found, "found" if found else "not found")

    try:
        from .tree_backend import tree_sitter_available

        available = tree_sitter_available()
        check("tree_sitter", available, "available" if available else "not installed")
    except Exception as exc:
        check("tree_sitter", False, str(exc))

    try:
        import watchfiles

        check("watchfiles", True, f"installed {getattr(watchfiles, '__version__', '')}")
    except Exception:
        check("watchfiles", False, "not installed; polling daemon still works")

    required = {"python", "database", "fts5"}
    return {
        "project_root": str(config.project_root),
        "schema_version": SCHEMA_VERSION,
        "ok": all(item["status"] == "ok" for item in checks if item["name"] in required),
        "checks": checks,
    }
