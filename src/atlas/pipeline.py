"""Deterministic end-to-end orchestration over structural index data."""

from __future__ import annotations

import sqlite3

from .context import developer_context, module_context, system_context
from .db import audit
from .router import route_request
from .store import impact_chain, memories
from .synthesis import converge_findings


def _fallback_file(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT path, 1 AS line FROM files ORDER BY path LIMIT 1"
    ).fetchone()
    if row is None:
        raise FileNotFoundError("index has no files; run atlas index first")
    return {"file": row["path"], "line": row["line"]}


def _candidate_file(route: dict, conn: sqlite3.Connection) -> dict:
    candidates = route.get("candidates", [])
    if candidates:
        return {
            "file": candidates[0]["file"],
            "line": candidates[0]["line"],
            "repo": candidates[0]["repo"],
            "name": candidates[0]["name"],
        }
    row = conn.execute(
        "SELECT path, 1 AS line FROM files ORDER BY path LIMIT 1"
    ).fetchone()
    if row is None:
        return {"file": None, "line": None, "repo": None, "name": None}
    return {"file": row["path"], "line": row["line"], "repo": None, "name": None}


def _build_contexts(
    conn: sqlite3.Connection,
    route: dict,
    candidate: dict,
    *,
    min_confidence: float,
    limit: int,
) -> dict:
    contexts: dict[str, dict] = {}
    repo = candidate.get("repo")
    contexts["system"] = system_context(
        conn,
        subsystem=None,
        repo=repo,
        limit=limit,
    )
    if candidate["file"]:
        contexts["module"] = module_context(conn, candidate["file"], limit=limit)
        contexts["developer"] = developer_context(
            conn,
            candidate["file"],
            symbol=candidate.get("name"),
            limit=limit,
        )
    if route["intent"] == "implementation" and candidate["file"]:
        contexts["developer"] = developer_context(
            conn,
            candidate["file"],
            symbol=candidate.get("name"),
            limit=limit,
        )
    if route["intent"] == "impact":
        contexts["impact"] = impact_chain(
            conn,
            candidate["name"],
            limit=limit,
            min_confidence=min_confidence,
        )
    if route["intent"] == "memory":
        contexts["memories"] = memories(conn, status="stale", limit=limit)
    return contexts


def _worker_findings(route: dict, candidate: dict, contexts: dict) -> list[dict]:
    findings: list[dict] = []
    evidence = (
        [{"file": candidate["file"], "line": candidate["line"]}]
        if candidate["file"]
        else []
    )

    developer = contexts.get("developer")
    if developer and developer.get("target_symbols"):
        names = ", ".join(item["name"] for item in developer["target_symbols"])
        findings.append(
            {
                "role": "developer",
                "claim": f"Indexed target symbols: {names}.",
                "confidence": 0.72,
                "evidence": evidence,
                "recommended_actions": ["inspect source through a scoped worker"],
            }
        )

    module = contexts.get("module")
    if module:
        findings.append(
            {
                "role": "module",
                "claim": (
                    f"Module context has {len(module['symbols'])} symbols, "
                    f"{len(module['outbound_imports'])} outbound imports, and "
                    f"{len(module['inbound_references'])} inbound references."
                ),
                "confidence": 0.75,
                "evidence": evidence,
                "recommended_actions": ["confirm the target module boundary"],
            }
        )

    system = contexts.get("system")
    if system and evidence:
        findings.append(
            {
                "role": "system",
                "claim": (
                    f"Indexed system scope contains {len(system['repos'])} repos "
                    f"and {len(system['cross_repo_edges'])} cross-repo edges."
                ),
                "confidence": 0.72,
                "evidence": evidence,
                "recommended_actions": ["review deployment or sync requirements"],
            }
        )

    impact = contexts.get("impact")
    if impact:
        findings.append(
            {
                "role": "architect",
                "claim": (
                    f"Import graph shows {impact['direct_reference_count']} direct "
                    f"and {impact['second_hop_reference_count']} second-hop references."
                ),
                "confidence": 0.78 if impact["first_hop"] else 0.52,
                "evidence": evidence,
                "warnings": impact.get("warnings", []),
                "recommended_actions": ["verify impact before changing contracts"],
            }
        )

    memory_rows = contexts.get("memories")
    if memory_rows:
        evidence_rows = []
        for memory in memory_rows:
            for item in memory["evidence"]:
                evidence_rows.append(
                    {"file": item["file"], "line": item["start_line"]}
                )
        if evidence_rows:
            findings.append(
                {
                    "role": "module",
                    "claim": f"{len(memory_rows)} stale memories require review.",
                    "confidence": 0.7,
                    "evidence": evidence_rows[:3],
                    "recommended_actions": ["review memory evidence and confirm validity"],
                }
            )
    return findings


def run_pipeline(
    conn: sqlite3.Connection,
    question: str,
    *,
    min_confidence: float = 0.65,
    limit: int = 50,
) -> dict:
    route = route_request(conn, question, limit=min(limit, 10))
    candidate = _candidate_file(route, conn)
    contexts = _build_contexts(
        conn,
        route,
        candidate,
        min_confidence=min_confidence,
        limit=limit,
    )
    findings = _worker_findings(route, candidate, contexts)
    warnings = list(route.get("warnings", []))
    convergence = None
    if findings:
        impact_symbol = candidate.get("name") if route["intent"] == "impact" else None
        convergence = converge_findings(
            conn,
            question,
            findings,
            route=route,
            impact_symbol=impact_symbol,
            min_confidence=min_confidence,
            limit=limit,
        )
    else:
        warnings.append("no structural worker findings; pipeline cannot converge")
    result = {
        "question": question.strip(),
        "route": route,
        "contexts": contexts,
        "workers": {"findings": findings},
        "convergence": convergence,
        "warnings": warnings,
    }
    audit(
        conn,
        "pipeline.run",
        {
            "intent": route["intent"],
            "role": route["role"],
            "finding_count": len(findings),
            "converged": convergence is not None,
        },
        actor="deterministic-pipeline",
    )
    conn.commit()
    return result
