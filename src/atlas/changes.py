"""Approved, auditable code-patch workflow."""

from __future__ import annotations

import difflib
import hashlib
import json
import pathlib
import shlex
import sqlite3
import subprocess

from .config import ProjectConfig
from .db import audit, utcnow
from .kernel import decide_approval, request_approval


def _file_row(conn: sqlite3.Connection, file_path: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT f.path, r.path AS repo_path, r.name AS repo_name
        FROM files f
        JOIN repos r ON r.id = f.repo_id
        WHERE f.path = ?
        """,
        (file_path,),
    ).fetchone()
    if row is None:
        raise FileNotFoundError(f"indexed file not found: {file_path}")
    return row


def _physical_path(row: sqlite3.Row) -> pathlib.Path:
    stable_path = str(row["path"])
    repo_name = str(row["repo_name"])
    relative = stable_path[len(repo_name) + 1:] if stable_path.startswith(repo_name + "/") else stable_path
    return pathlib.Path(row["repo_path"]) / relative


def propose_patch(
    conn: sqlite3.Connection,
    config: ProjectConfig,
    file_path: str,
    *,
    old_text: str,
    new_text: str,
    title: str,
    created_by: str,
) -> dict:
    if not title.strip():
        raise ValueError("title must be non-empty")
    if not created_by.strip():
        raise ValueError("created_by must be non-empty")
    row = _file_row(conn, file_path)
    physical = _physical_path(row)
    if not physical.is_file():
        raise FileNotFoundError(f"physical file missing: {physical}")
    try:
        current = physical.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read target file: {exc}") from exc
    if old_text not in current:
        raise ValueError("old_text is not present exactly once-safe in the target file")
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        current.replace(old_text, new_text, 1).splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    )
    diff_text = "".join(diff)
    patch_key = hashlib.sha256(
        f"{file_path}:{old_text}:{new_text}".encode("utf-8")
    ).hexdigest()[:20]
    conn.execute(
        """
        INSERT INTO patches(
          patch_key, title, file_path, old_text, new_text, diff_text,
          status, created_by, created_at
        ) VALUES(?,?,?,?,?,?, 'pending', ?, ?)
        ON CONFLICT(patch_key) DO UPDATE SET
          title=excluded.title, old_text=excluded.old_text,
          new_text=excluded.new_text, diff_text=excluded.diff_text,
          status='pending', created_at=excluded.created_at
        """,
        (patch_key, title.strip(), file_path, old_text, new_text, diff_text, created_by, utcnow()),
    )
    approval = request_approval(
        conn,
        "apply_patch",
        {"patch_key": patch_key, "file_path": file_path, "title": title.strip()},
        requested_by=created_by,
    )
    conn.execute("UPDATE patches SET approval_id = ? WHERE patch_key = ?", (approval["approval_id"], patch_key))
    audit(conn, "patch.proposed", {"patch_key": patch_key, "file": file_path}, actor=created_by)
    conn.commit()
    return {
        "patch_key": patch_key,
        "approval": approval,
        "diff": diff_text,
    }


def decide_patch(
    conn: sqlite3.Connection,
    patch_key: str,
    decision: str,
    *,
    approver: str,
    reason: str,
) -> dict:
    row = conn.execute("SELECT * FROM patches WHERE patch_key = ?", (patch_key,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"patch not found: {patch_key}")
    if row["approval_id"] is None:
        raise ValueError("patch has no approval request")
    result = decide_approval(conn, int(row["approval_id"]), decision, approver=approver, reason=reason)
    conn.execute(
        "UPDATE patches SET status = ? WHERE patch_key = ?",
        ("approved" if decision == "approved" else "rejected", patch_key),
    )
    conn.commit()
    return {"patch_key": patch_key, **result}


def apply_patch(
    conn: sqlite3.Connection,
    patch_key: str,
    *,
    applied_by: str,
    test_command: str | None = None,
    timeout: float = 300.0,
) -> dict:
    row = conn.execute("SELECT * FROM patches WHERE patch_key = ?", (patch_key,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"patch not found: {patch_key}")
    if row["status"] != "approved":
        raise ValueError(f"patch is not approved: {row['status']}")
    approval = conn.execute("SELECT * FROM approvals WHERE id = ?", (row["approval_id"],)).fetchone()
    if approval is None or approval["status"] != "approved":
        raise ValueError("patch approval is not approved")
    target = _file_row(conn, row["file_path"])
    physical = _physical_path(target)
    try:
        current = physical.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read target file: {exc}") from exc
    if current.count(row["old_text"]) != 1:
        raise ValueError("target text has changed; patch can no longer be applied safely")
    if test_command:
        command = shlex.split(test_command)
        result = subprocess.run(command, cwd=str(physical.parent), capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(
                f"sandbox test failed with exit {result.returncode}: {result.stdout[-2000:]}\n{result.stderr[-2000:]}"
            )
    physical.write_text(current.replace(row["old_text"], row["new_text"], 1), encoding="utf-8")
    conn.execute(
        "UPDATE patches SET status = 'applied', applied_at = ? WHERE patch_key = ?",
        (utcnow(), patch_key),
    )
    audit(
        conn,
        "patch.applied",
        {"patch_key": patch_key, "file": row["file_path"], "tested": bool(test_command)},
        actor=applied_by,
    )
    conn.commit()
    return {"patch_key": patch_key, "status": "applied", "file": row["file_path"]}


def list_patches(conn: sqlite3.Connection, status: str | None = None, limit: int = 50) -> list[dict]:
    if status is not None and status not in {"pending", "approved", "rejected", "applied"}:
        raise ValueError("patch status is invalid")
    sql = "SELECT * FROM patches"
    params: list[object] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    return [
        {key: value for key, value in dict(row).items() if key not in {"old_text", "new_text"}}
        for row in conn.execute(sql, params)
    ]
