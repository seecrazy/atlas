"""Offline, bounded evolution: distillation, variants, and frozen evaluation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from .config import ProjectConfig
from .db import audit, utcnow
from .kernel import request_approval
from .router import evaluate_router, route_request


def distill_strategy_rules(conn: sqlite3.Connection, *, limit: int = 100) -> dict:
    rows = conn.execute(
        """
        SELECT payload_json, created_at
        FROM audit_events
        WHERE event_type = 'router.request'
        ORDER BY id DESC LIMIT ?
        """,
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    candidates = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        signals = payload.get("signals", [])
        if not signals or "impact_keyword" not in signals:
            continue
        pattern = "impact"
        key = f"rule:{hashlib.sha256((pattern + payload['role']).encode()).hexdigest()[:16]}"
        now = utcnow()
        conn.execute(
            """
            INSERT INTO strategy_rules(rule_key, pattern, intent, role, utility_score, hits, source, created_at, updated_at)
            VALUES(?, ?, 'impact', ?, 0.55, 1, 'audit-distiller', ?, ?)
            ON CONFLICT(rule_key) DO UPDATE SET
              hits = hits + 1,
              utility_score = MIN(1.0, utility_score + 0.01),
              updated_at = ?
            """,
            (key, pattern, payload["role"], now, now, now),
        )
        candidates.append({"rule_key": key, "intent": "impact", "role": payload["role"]})
    audit(conn, "strategy.distilled", {"count": len(candidates)})
    conn.commit()
    return {"rules": candidates, "count": len(candidates)}


def create_variant(
    conn: sqlite3.Connection,
    *,
    object_type: str,
    name: str,
    payload: dict,
    parent_key: str | None = None,
) -> dict:
    if object_type not in {"router_terms", "strategy_rule", "context_template"}:
        raise ValueError("object_type must be router_terms, strategy_rule, or context_template")
    if not name.strip():
        raise ValueError("variant name must be non-empty")
    variant_key = f"{object_type}:{hashlib.sha256(name.encode()).hexdigest()[:16]}"
    conn.execute(
        """
        INSERT INTO evolution_variants(
          variant_key, object_type, name, payload_json, status, parent_key, created_at
        ) VALUES(?,?,?,?, 'candidate', ?, ?)
        ON CONFLICT(variant_key) DO UPDATE SET
          payload_json=excluded.payload_json, name=excluded.name, status='candidate',
          parent_key=excluded.parent_key, created_at=excluded.created_at
        """,
        (variant_key, object_type, name.strip(), json.dumps(payload, sort_keys=True), parent_key, utcnow()),
    )
    audit(conn, "evolution.variant_created", {"variant_key": variant_key})
    conn.commit()
    return {"variant_key": variant_key, "status": "candidate"}


def evaluate_variant(
    conn: sqlite3.Connection,
    config: ProjectConfig,
    variant_key: str,
    *,
    record: bool = True,
) -> dict:
    row = conn.execute("SELECT * FROM evolution_variants WHERE variant_key = ?", (variant_key,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"variant not found: {variant_key}")
    payload = json.loads(row["payload_json"])
    score = evaluate_router(conn, record=False)["intent_accuracy"]
    if row["object_type"] == "router_terms":
        terms = payload.get("add_terms", [])
        if not isinstance(terms, list) or not all(isinstance(item, str) for item in terms):
            raise ValueError("router_terms payload.add_terms must be an array of strings")
        # Evaluation remains frozen. The variant does not mutate runtime rules;
        # acceptance merely marks it safe for a human to promote into config.
        for term in terms:
            if re.search(r"[\W_]", term) and term not in {"cross-repo"}:
                raise ValueError("router term variants must be plain words")
        score = round(min(1.0, score + 0.01 * len(terms)), 4)
    status = "accepted" if score >= 0.8 else "rejected"
    conn.execute(
        "UPDATE evolution_variants SET score = ?, status = ?, evaluated_at = ? WHERE variant_key = ?",
        (score, status, utcnow(), variant_key),
    )
    if record:
        audit(conn, "evolution.variant_evaluated", {"variant_key": variant_key, "score": score, "status": status})
    conn.commit()
    return {"variant_key": variant_key, "score": score, "status": status}


def list_variants(conn: sqlite3.Connection, status: str | None = None, limit: int = 50) -> list[dict]:
    if status is not None and status not in {"candidate", "accepted", "rejected", "promoted", "rolled_back"}:
        raise ValueError("variant status is invalid")
    sql = "SELECT * FROM evolution_variants"
    params: list[object] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    return [
        {**dict(row), "payload": json.loads(row["payload_json"])}
        for row in conn.execute(sql, params)
    ]


def propose_promotion(
    conn: sqlite3.Connection,
    variant_key: str,
    *,
    requested_by: str,
) -> dict:
    row = conn.execute("SELECT * FROM evolution_variants WHERE variant_key = ?", (variant_key,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"variant not found: {variant_key}")
    if row["status"] != "accepted":
        raise ValueError(f"only accepted variants can be promoted: {row['status']}")
    if row["object_type"] != "strategy_rule":
        raise ValueError("only strategy_rule variants support runtime promotion")
    payload = json.loads(row["payload_json"])
    rule_payload = {
        "variant_key": variant_key,
        "pattern": str(payload.get("pattern", "")).strip(),
        "intent": payload.get("intent"),
        "role": payload.get("role"),
        "utility_score": payload.get("utility_score", row["score"]),
    }
    if not rule_payload["pattern"] or rule_payload["intent"] not in {"locate", "impact", "implementation", "memory"}:
        raise ValueError("strategy rule variant requires pattern and valid intent")
    if rule_payload["role"] not in {"architect", "system", "module", "developer"}:
        raise ValueError("strategy rule variant requires a valid role")
    return request_approval(conn, "promote_variant", rule_payload, requested_by=requested_by)


def promote_variant(
    conn: sqlite3.Connection,
    variant_key: str,
    *,
    approval_key: str,
    promoted_by: str,
) -> dict:
    approval = conn.execute("SELECT * FROM approvals WHERE approval_key = ?", (approval_key,)).fetchone()
    if approval is None or approval["status"] != "approved" or approval["action_type"] != "promote_variant":
        raise ValueError("promotion approval is not approved")
    payload = json.loads(approval["payload_json"])
    if payload.get("variant_key") != variant_key:
        raise ValueError("promotion approval does not match variant")
    rule_key = f"variant:{variant_key}"
    now = utcnow()
    conn.execute(
        """
        INSERT INTO strategy_rules(
          rule_key, pattern, intent, role, utility_score, hits, source, created_at, updated_at
        ) VALUES(?,?,?,?,?,0,?, ?, ?)
        ON CONFLICT(rule_key) DO UPDATE SET
          pattern=excluded.pattern, intent=excluded.intent, role=excluded.role,
          utility_score=excluded.utility_score, updated_at=excluded.updated_at
        """,
        (
            rule_key, payload["pattern"], payload["intent"], payload["role"],
            float(payload["utility_score"]), variant_key, now, now,
        ),
    )
    conn.execute(
        "UPDATE evolution_variants SET status = 'promoted', evaluated_at = ? WHERE variant_key = ?",
        (now, variant_key),
    )
    audit(conn, "evolution.variant_promoted", {"variant_key": variant_key, "rule_key": rule_key}, actor=promoted_by)
    conn.commit()
    return {"variant_key": variant_key, "rule_key": rule_key, "status": "promoted"}


def propose_rollback(
    conn: sqlite3.Connection,
    variant_key: str,
    *,
    requested_by: str,
    reason: str,
) -> dict:
    if not reason.strip():
        raise ValueError("rollback reason must be non-empty")
    row = conn.execute("SELECT * FROM evolution_variants WHERE variant_key = ?", (variant_key,)).fetchone()
    if row is None or row["status"] != "promoted":
        raise ValueError("only promoted variants can be rolled back")
    return request_approval(
        conn,
        "rollback_variant",
        {"variant_key": variant_key, "reason": reason},
        requested_by=requested_by,
    )


def rollback_variant(
    conn: sqlite3.Connection,
    variant_key: str,
    *,
    approval_key: str,
    rolled_back_by: str,
) -> dict:
    approval = conn.execute("SELECT * FROM approvals WHERE approval_key = ?", (approval_key,)).fetchone()
    if approval is None or approval["status"] != "approved" or approval["action_type"] != "rollback_variant":
        raise ValueError("rollback approval is not approved")
    payload = json.loads(approval["payload_json"])
    if payload.get("variant_key") != variant_key:
        raise ValueError("rollback approval does not match variant")
    rule_key = f"variant:{variant_key}"
    conn.execute("DELETE FROM strategy_rules WHERE rule_key = ? AND source = ?", (rule_key, variant_key))
    conn.execute("UPDATE evolution_variants SET status = 'rolled_back', evaluated_at = ? WHERE variant_key = ?", (utcnow(), variant_key))
    audit(
        conn,
        "evolution.variant_rolled_back",
        {"variant_key": variant_key, "rule_key": rule_key, "reason": payload.get("reason")},
        actor=rolled_back_by,
    )
    conn.commit()
    return {"variant_key": variant_key, "rule_key": rule_key, "status": "rolled_back"}
