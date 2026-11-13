"""Kernel-level budgets, immutable rules, approvals, and verification."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, field

from .config import ProjectConfig
from .db import audit, utcnow


HIGH_RISK_ACTIONS = {
    "apply_patch",
    "create_pr",
    "promote_variant",
    "rollback_variant",
    "delete_file",
    "external_send",
    "permission_change",
    "payment",
    "runtime_self_modify",
}
MEDIUM_RISK_ACTIONS = {"propose_patch", "create_pr", "memory_generate"}
ALLOWED_ACTIONS = HIGH_RISK_ACTIONS | MEDIUM_RISK_ACTIONS | {
    "index", "search", "references", "impact", "memory_review", "evolve_offline",
}


@dataclass
class Budget:
    max_seconds: float
    max_calls: int
    max_tokens: int
    started_at: float = field(default_factory=time.monotonic)
    calls: int = 0
    tokens: int = 0

    @classmethod
    def from_config(cls, config: ProjectConfig) -> "Budget":
        return cls(
            max_seconds=float(config.budgets["max_seconds"]),
            max_calls=int(config.budgets["max_calls"]),
            max_tokens=int(config.budgets["max_tokens"]),
        )

    def spend(self, *, calls: int = 1, tokens: int = 0) -> None:
        self.calls += calls
        self.tokens += tokens
        if self.calls > self.max_calls:
            raise RuntimeError("kernel budget exceeded: call limit")
        if self.tokens > self.max_tokens:
            raise RuntimeError("kernel budget exceeded: token limit")
        if time.monotonic() - self.started_at > self.max_seconds:
            raise RuntimeError("kernel budget exceeded: time limit")

    def usage(self) -> dict:
        return {
            "elapsed_seconds": round(time.monotonic() - self.started_at, 3),
            "calls": self.calls,
            "tokens": self.tokens,
            "max_seconds": self.max_seconds,
            "max_calls": self.max_calls,
            "max_tokens": self.max_tokens,
        }


def risk_for(action_type: str) -> str:
    if action_type in HIGH_RISK_ACTIONS:
        return "high"
    if action_type in MEDIUM_RISK_ACTIONS:
        return "medium"
    if action_type in ALLOWED_ACTIONS:
        return "low"
    raise ValueError(f"unknown action type: {action_type}")


def request_approval(
    conn: sqlite3.Connection,
    action_type: str,
    payload: dict,
    *,
    requested_by: str,
) -> dict:
    risk = risk_for(action_type)
    approval_key = f"{action_type}:{hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]}"
    now = utcnow()
    conn.execute(
        """
        INSERT INTO approvals(
          approval_key, action_type, risk, payload_json, status,
          requested_by, created_at
        ) VALUES(?,?,?,?, 'pending', ?, ?)
        ON CONFLICT(approval_key) DO NOTHING
        """,
        (approval_key, action_type, risk, json.dumps(payload, ensure_ascii=False, sort_keys=True), requested_by, now),
    )
    row = conn.execute("SELECT * FROM approvals WHERE approval_key = ?", (approval_key,)).fetchone()
    audit(
        conn,
        "approval.requested",
        {"approval_key": approval_key, "action_type": action_type, "risk": risk},
        actor=requested_by,
    )
    conn.commit()
    return {
        "approval_id": row["id"],
        "approval_key": approval_key,
        "status": row["status"],
        "risk": row["risk"],
    }


def decide_approval(
    conn: sqlite3.Connection,
    approval_id: int,
    decision: str,
    *,
    approver: str,
    reason: str,
) -> dict:
    if decision not in {"approved", "rejected"}:
        raise ValueError("approval decision must be approved or rejected")
    if not approver.strip() or not reason.strip():
        raise ValueError("approver and reason must be non-empty")
    row = conn.execute("SELECT * FROM approvals WHERE id = ?", (int(approval_id),)).fetchone()
    if row is None:
        raise FileNotFoundError(f"approval not found: {approval_id}")
    if row["status"] != "pending":
        raise ValueError(f"approval is not pending: {row['status']}")
    conn.execute(
        "UPDATE approvals SET status = ?, approver = ?, reason = ?, decided_at = ? WHERE id = ?",
        (decision, approver.strip(), reason.strip(), utcnow(), int(approval_id)),
    )
    audit(
        conn,
        "approval.decided",
        {"approval_id": int(approval_id), "decision": decision},
        actor=approver,
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM approvals WHERE id = ?", (int(approval_id),)).fetchone()
    return {
        "approval_id": updated["id"],
        "status": updated["status"],
        "action_type": updated["action_type"],
        "risk": updated["risk"],
    }


def list_approvals(conn: sqlite3.Connection, status: str | None = None, limit: int = 50) -> list[dict]:
    if status is not None and status not in {"pending", "approved", "rejected"}:
        raise ValueError("approval status must be pending, approved, or rejected")
    sql = "SELECT * FROM approvals"
    params: list[object] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    return [
        {
            **dict(row),
            "payload": json.loads(row["payload_json"]),
        }
        for row in conn.execute(sql, params)
    ]


def verify_finding(conn: sqlite3.Connection, finding: dict) -> dict:
    evidence = finding.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return {"valid": False, "reason": "finding has no evidence"}
    for item in evidence:
        if not isinstance(item, dict) or not item.get("file"):
            return {"valid": False, "reason": "evidence entry has no file"}
        row = conn.execute("SELECT id FROM files WHERE path = ?", (item["file"],)).fetchone()
        if row is None:
            return {"valid": False, "reason": f"evidence file is unindexed: {item['file']}"}
    return {"valid": True, "reason": "all evidence files are indexed"}


def private_score(result: dict) -> float:
    """Kernel-only heuristic. Callers receive only the score, not the formula."""
    findings = result.get("ordered_findings", result.get("workers", {}).get("findings", []))
    if not findings:
        return 0.0
    confidences = [float(item.get("confidence", 0.0)) for item in findings]
    evidence_counts = [len(item.get("evidence", [])) for item in findings]
    score = sum(confidences) / len(confidences)
    score += min(0.15, sum(evidence_counts) * 0.01)
    return round(min(1.0, max(0.0, score)), 4)
