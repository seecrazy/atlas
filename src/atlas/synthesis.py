"""Upward role-finding convergence for ATLAS workers."""

from __future__ import annotations

import sqlite3

from .db import audit
from .store import impact_chain


ROLE_ORDER = ["developer", "module", "system", "architect"]
ROLE_BY_LAYER = {
    "developer": 0,
    "module": 1,
    "system": 2,
    "architect": 3,
}


def _confidence(value: float) -> float:
    value = float(value)
    if value < 0.0 or value > 1.0:
        raise ValueError("finding confidence must be between 0.0 and 1.0")
    return value


def _validate_evidence(conn: sqlite3.Connection, evidence: object, finding_index: int) -> list[dict]:
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(f"finding[{finding_index}].evidence must be a non-empty array")
    normalized: list[dict] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise ValueError(f"finding[{finding_index}].evidence[{index}] must be an object")
        file_path = item.get("file")
        line = item.get("line", 1)
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError(
                f"finding[{finding_index}].evidence[{index}].file must be a non-empty string"
            )
        try:
            line = int(line)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"finding[{finding_index}].evidence[{index}].line must be an integer"
            ) from exc
        if line < 1:
            raise ValueError(
                f"finding[{finding_index}].evidence[{index}].line must be positive"
            )
        row = conn.execute(
            """
            SELECT f.id, f.path, r.name AS repo, r.subsystem
            FROM files f
            JOIN repos r ON r.id = f.repo_id
            WHERE f.path = ?
            """,
            (file_path.strip(),),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(
                f"finding[{finding_index}].evidence[{index}] references an unindexed file: {file_path}"
            )
        claimed_repo = item.get("repo")
        if claimed_repo is not None and claimed_repo != row["repo"]:
            raise ValueError(
                f"finding[{finding_index}].evidence[{index}].repo does not match indexed repo: {row['repo']}"
            )
        normalized.append(
            {
                "file": row["path"],
                "repo": row["repo"],
                "subsystem": row["subsystem"],
                "line": line,
                "note": item.get("note"),
            }
        )
    return normalized


def _validate_finding(conn: sqlite3.Connection, finding: object, index: int) -> dict:
    if not isinstance(finding, dict):
        raise ValueError(f"finding[{index}] must be an object")
    role = finding.get("role")
    claim = finding.get("claim")
    if role not in ROLE_BY_LAYER:
        raise ValueError(
            f"finding[{index}].role must be one of: {', '.join(ROLE_ORDER)}"
        )
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError(f"finding[{index}].claim must be a non-empty string")
    confidence = _confidence(finding.get("confidence", 0.6))
    evidence = _validate_evidence(conn, finding.get("evidence"), index)
    warnings = finding.get("warnings", [])
    actions = finding.get("recommended_actions", [])
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise ValueError(f"finding[{index}].warnings must be an array of strings")
    if not isinstance(actions, list) or not all(isinstance(item, str) for item in actions):
        raise ValueError(
            f"finding[{index}].recommended_actions must be an array of strings"
        )
    return {
        "role": role,
        "claim": claim.strip(),
        "confidence": round(confidence, 2),
        "evidence": evidence,
        "warnings": warnings,
        "recommended_actions": actions,
    }


def converge_findings(
    conn: sqlite3.Connection,
    question: str,
    findings: list[dict],
    *,
    route: dict | None = None,
    impact_symbol: str | None = None,
    min_confidence: float | None = 0.65,
    limit: int | None = 50,
) -> dict:
    """Validate and converge worker findings in upward layer order."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    if not isinstance(findings, list) or not findings:
        raise ValueError("findings must be a non-empty array")
    limit = max(1, min(int(limit or 50), 200))
    validated = [_validate_finding(conn, item, index) for index, item in enumerate(findings)]
    ordered = sorted(validated, key=lambda item: ROLE_BY_LAYER[item["role"]])[:limit]

    role_summary = {role: 0 for role in ROLE_ORDER}
    for item in ordered:
        role_summary[item["role"]] += 1
    missing_layers = [role for role in ROLE_ORDER if role_summary[role] == 0]

    evidence_files = sorted(
        {item["file"] for finding in ordered for item in finding["evidence"]}
    )[:limit]
    evidence_repos = sorted(
        {item["repo"] for finding in ordered for item in finding["evidence"]}
    )
    evidence_subsystems = sorted(
        {item["subsystem"] for finding in ordered for item in finding["evidence"]}
    )

    propagation = None
    if impact_symbol:
        propagation = impact_chain(
            conn,
            impact_symbol,
            limit=limit,
            min_confidence=min_confidence,
        )

    grouped = {
        role: [
            {
                "claim": item["claim"],
                "confidence": item["confidence"],
                "evidence": item["evidence"],
                "warnings": item["warnings"],
                "recommended_actions": item["recommended_actions"],
            }
            for item in ordered
            if item["role"] == role
        ]
        for role in ROLE_ORDER
    }

    average_confidence = sum(item["confidence"] for item in ordered) / len(ordered)
    warnings = []
    if missing_layers:
        warnings.append("missing upward layers: " + ", ".join(missing_layers))
    if any(item["confidence"] < 0.65 for item in ordered):
        warnings.append("one or more findings have confidence below 0.65")
    if propagation and not propagation["first_hop"]:
        warnings.append("impact chain has no direct references")

    result = {
        "question": question.strip(),
        "route": route,
        "ordered_findings": ordered,
        "role_summary": role_summary,
        "missing_layers": missing_layers,
        "evidence_files": evidence_files,
        "evidence_repos": evidence_repos,
        "evidence_subsystems": evidence_subsystems,
        "impact_propagation": propagation,
        "answer_sections": {
            "implementation_findings": grouped["developer"],
            "module_notes": grouped["module"],
            "system_notes": grouped["system"],
            "architecture_notes": grouped["architect"],
        },
        "confidence": round(average_confidence, 2),
        "warnings": warnings,
    }
    audit(
        conn,
        "synthesis.converged",
        {
            "finding_count": len(ordered),
            "role_summary": role_summary,
            "missing_layers": missing_layers,
            "has_impact_propagation": propagation is not None,
        },
        actor="atlas-converger",
    )
    conn.commit()
    return result
