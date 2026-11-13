"""Approved, auditable code-patch workflow."""

from __future__ import annotations

import difflib
import hashlib
import json
import pathlib
import shlex
import shutil
import sqlite3
import subprocess
import tempfile

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


def _ensure_inside_repo(physical: pathlib.Path, repo_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(repo_path).resolve()
    resolved = physical.resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"target path escapes repository: {physical} -> {resolved}") from exc
    _ensure_no_symlink_components(physical)


def _ensure_no_symlink_components(physical: pathlib.Path) -> None:
    if any(part.is_symlink() for part in physical.parents):
        raise ValueError(f"symlinked path is not allowed: {physical}")


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
    _ensure_inside_repo(physical, pathlib.Path(row["repo_path"]))
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
    config: ProjectConfig,
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
        if test_command.strip() not in config.allowed_test_commands:
            raise ValueError(
                "test_command is not in config.allowed_test_commands; "
                "add the exact command to project configuration"
            )
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


def _git(repo_path: pathlib.Path, args: list[str], timeout: float = 60.0) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout.strip()


def _gh(repo_path: pathlib.Path, args: list[str], timeout: float = 60.0) -> str:
    result = subprocess.run(
        ["gh", *args],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout.strip()


def propose_publication(
    conn: sqlite3.Connection,
    config: ProjectConfig,
    patch_key: str,
    *,
    remote: str,
    base_branch: str,
    branch: str,
    title: str,
    body: str = "",
    requested_by: str,
) -> dict:
    row = conn.execute("SELECT * FROM patches WHERE patch_key = ?", (patch_key,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"patch not found: {patch_key}")
    if row["status"] != "applied":
        raise ValueError(f"patch must be applied before publication: {row['status']}")
    if not branch.strip() or not title.strip():
        raise ValueError("branch and title must be non-empty")
    payload = {
        "patch_key": patch_key,
        "remote": remote,
        "base_branch": base_branch,
        "branch": branch,
        "title": title,
        "body": body,
    }
    return request_approval(conn, "create_pr", payload, requested_by=requested_by)


def publish_patch(
    conn: sqlite3.Connection,
    config: ProjectConfig,
    patch_key: str,
    *,
    remote: str,
    base_branch: str,
    branch: str,
    title: str,
    body: str = "",
    approval_key: str,
    published_by: str,
    push: bool = True,
    create_pr: bool = True,
    timeout: float = 300.0,
) -> dict:
    approval = conn.execute("SELECT * FROM approvals WHERE approval_key = ?", (approval_key,)).fetchone()
    if approval is None or approval["status"] != "approved":
        raise ValueError("publication approval is not approved")
    payload = json.loads(approval["payload_json"])
    expected = {
        "patch_key": patch_key,
        "remote": remote,
        "base_branch": base_branch,
        "branch": branch,
        "title": title,
        "body": body,
    }
    if payload != expected:
        raise ValueError("publication approval payload does not match request")
    patch = conn.execute("SELECT * FROM patches WHERE patch_key = ?", (patch_key,)).fetchone()
    if patch is None or patch["status"] != "applied":
        raise ValueError("only applied patches can be published")
    target = _file_row(conn, patch["file_path"])
    source_physical = _physical_path(target)
    _ensure_inside_repo(source_physical, pathlib.Path(target["repo_path"]))
    repo_path = pathlib.Path(target["repo_path"])
    relative = str(target["path"]).replace(f"{target['repo_name']}/", "", 1)
    commit_message = f"{title}\n\nATLAS patch: {patch_key}\nApproved by: {published_by}"
    pushed = False
    pr_url = None
    with tempfile.TemporaryDirectory(prefix="atlas-publish-") as temp:
        worktree_path = pathlib.Path(temp) / "worktree"
        _git(
            repo_path,
            ["worktree", "add", "--detach", str(worktree_path), base_branch],
            timeout=timeout,
        )
        try:
            _git(worktree_path, ["checkout", "-B", branch, base_branch], timeout=timeout)
            destination = worktree_path / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_physical, destination)
            _git(worktree_path, ["add", "--", relative], timeout=timeout)
            _git(worktree_path, ["commit", "-m", commit_message], timeout=timeout)
            if push:
                _git(worktree_path, ["push", "-u", remote, branch], timeout=timeout)
                pushed = True
            if create_pr:
                pr_url = _gh(
                    worktree_path,
                    ["pr", "create", "--base", base_branch, "--head", branch, "--title", title, "--body", body or commit_message],
                    timeout=timeout,
                )
        finally:
            _git(repo_path, ["worktree", "remove", "--force", str(worktree_path)], timeout=timeout)
    audit(
        conn,
        "patch.published",
        {"patch_key": patch_key, "branch": branch, "remote": remote, "pushed": pushed, "pr_url": pr_url},
        actor=published_by,
    )
    conn.commit()
    return {"patch_key": patch_key, "branch": branch, "pushed": pushed, "pr_url": pr_url}
