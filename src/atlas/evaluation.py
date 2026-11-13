"""Frozen production evaluation harness for semantic memory and agents."""

from __future__ import annotations

import json
from pathlib import Path

import sqlite3

from .agents import run_layered_agents
from .config import ProjectConfig
from .db import audit, utcnow
from .router import evaluate_router


CASES_PATH = Path(__file__).with_name("data") / "evaluation_cases.json"


def load_evaluation_cases(path: str | Path | None = None) -> dict:
    path = Path(path) if path else CASES_PATH
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("evaluation cases must be a JSON object")
    for key in ("semantic", "agents"):
        if key not in data or not isinstance(data[key], list):
            raise ValueError(f"evaluation cases require {key} array")
    return data


def _prf(matched: int, produced: int, expected: int) -> tuple[float, float, float]:
    precision = matched / produced if produced else 0.0
    recall = matched / expected if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def evaluate_semantic_memories(
    conn: sqlite3.Connection,
    cases: list[dict] | None = None,
) -> dict:
    cases = cases or []
    results = []
    total_precision = total_recall = total_f1 = 0.0
    for index, case in enumerate(cases):
        scope = str(case.get("scope", "")).strip()
        expected = list(case.get("expected_terms", []))
        rows = conn.execute(
            "SELECT memory_key, claim FROM memories WHERE claim LIKE ? LIMIT 20",
            (f"%{scope}%",),
        ).fetchall()
        produced_terms = {
            term
            for row in rows
            for term in str(row["claim"]).replace(",", " ").replace("(", " ").replace(")", " ").split()
        }
        matched = [term for term in expected if term in produced_terms]
        precision, recall, f1 = _prf(len(matched), len(produced_terms), len(expected))
        results.append(
            {
                "scope": scope,
                "expected_terms": expected,
                "matched_terms": matched,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "memory_count": len(rows),
            }
        )
        total_precision += precision
        total_recall += recall
        total_f1 += f1
    count = len(cases)
    return {
        "case_count": count,
        "precision": round(total_precision / count, 4) if count else None,
        "recall": round(total_recall / count, 4) if count else None,
        "f1": round(total_f1 / count, 4) if count else None,
        "results": results,
    }


def evaluate_agents(
    conn: sqlite3.Connection,
    config: ProjectConfig,
    cases: list[dict] | None = None,
    *,
    use_llm: bool = False,
) -> dict:
    cases = cases or []
    results = []
    hits = 0
    evidence_covered = 0
    confidence_total = 0.0
    for case in cases:
        question = str(case.get("question", "")).strip()
        expected_symbols = list(case.get("expected_symbols", []))
        expected_repo = case.get("expected_repo")
        result = run_layered_agents(
            conn,
            config,
            question,
            use_llm=use_llm,
            min_confidence=0.65,
        )
        candidate_names = {item["name"] for item in result["route"].get("candidates", [])}
        finding_names = {
            term
            for finding in result["workers"]["findings"]
            for term in str(finding.get("claim", "")).replace(",", " ").split()
        }
        matched = [symbol for symbol in expected_symbols if symbol in candidate_names or symbol in finding_names]
        evidence_files = [item["file"] for finding in result["workers"]["findings"] for item in finding.get("evidence", [])]
        repo_covered = not expected_repo or any(expected_repo in item for item in evidence_files)
        hit = bool(matched) and repo_covered
        if hit:
            hits += 1
        if evidence_files:
            evidence_covered += 1
        confidences = [finding.get("confidence", 0.0) for finding in result["workers"]["findings"]]
        confidence_total += sum(confidences) / len(confidences) if confidences else 0.0
        results.append(
            {
                "question": question,
                "expected_symbols": expected_symbols,
                "matched_symbols": matched,
                "expected_repo": expected_repo,
                "repo_covered": repo_covered,
                "hit": hit,
                "finding_count": len(result["workers"]["findings"]),
                "evidence_file_count": len(evidence_files),
            }
        )
    count = len(cases)
    return {
        "case_count": count,
        "hit_rate": round(hits / count, 4) if count else None,
        "evidence_coverage": round(evidence_covered / count, 4) if count else None,
        "average_confidence": round(confidence_total / count, 4) if count else None,
        "use_llm": use_llm,
        "results": results,
    }


def run_production_evaluation(
    conn: sqlite3.Connection,
    config: ProjectConfig,
    *,
    cases_path: str | Path | None = None,
    use_llm: bool = False,
    record: bool = True,
) -> dict:
    cases = load_evaluation_cases(cases_path)
    semantic = evaluate_semantic_memories(conn, cases["semantic"])
    agents = evaluate_agents(conn, config, cases["agents"], use_llm=use_llm)
    router = evaluate_router(conn, record=False)
    result = {
        "semantic": semantic,
        "agents": agents,
        "router": router,
        "evaluated_at": utcnow(),
        "use_llm": use_llm,
    }
    if record:
        audit(
            conn,
            "production.evaluation",
            {
                "semantic_f1": semantic["f1"],
                "agent_hit_rate": agents["hit_rate"],
                "router_intent_accuracy": router["intent_accuracy"],
                "use_llm": use_llm,
            },
            actor="production-evaluator",
        )
        conn.commit()
    return result
