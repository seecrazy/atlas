"""Deterministic request routing based on indexed structural signals."""

from __future__ import annotations

import re
import sqlite3
import json
from pathlib import Path

from .db import audit
from .store import references, search


STOP_TERMS = {
    "and", "the", "this", "that", "what", "where", "which", "who", "why",
    "how", "does", "did", "can", "could", "should", "would", "there", "here",
    "with", "from", "into", "about", "please", "help", "code", "file",
    "module", "repo", "repository", "system", "project", "used", "using",
    "change", "changed", "changing", "make", "made", "have", "has", "are",
    "when", "after", "before", "return", "returns", "read", "find", "show",
}

IMPACT_TERMS = {
    "impact", "affected", "affects", "references", "callers", "caller",
    "reference", "references", "caller", "callers",
    "reference", "references", "caller", "callers",
    "dependency", "dependencies", "dependent", "depend", "depends", "consumer", "consumers",
    "consume", "consumes", "调用", "引用", "影响",
    "依赖", "受影响", "谁用",
}
IMPLEMENTATION_TERMS = {
    "implementation", "implemented", "function", "method", "body", "bug", "fix", "root",
    "cause", "debug", "实现", "函数体", "方法体", "修复", "根因", "排查",
    "调试",
}
MEMORY_TERMS = {
    "memory", "memories", "lesson", "decision", "adr", "revert", "记忆", "教训",
    "决策", "复核", "回滚",
}
ARCHITECTURE_TERMS = {
    "subsystem", "subsystems", "architecture", "contract", "contracts",
    "cross_repo", "cross-repo", "cross_repo", "子系统", "架构", "契约",
    "跨仓", "跨子系统",
}
SYSTEM_TERMS = {
    "repo", "repository", "service", "module", "warehouse", "仓库",
    "服务", "模块", "哪个仓", "哪里", "在哪",
}


def _candidate_terms(question: str) -> list[str]:
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", question)
    seen = set()
    terms = []
    for term in raw:
        lower = term.lower()
        if lower in STOP_TERMS:
            continue
        if lower not in seen:
            seen.add(lower)
            terms.append(term)
    return terms[:10]


def _contains_any(text: str, terms: set[str]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in terms)


def _signal_labels(question: str, candidates: list[dict], refs: list[dict]) -> list[str]:
    signals = []
    if _contains_any(question, IMPACT_TERMS):
        signals.append("impact_keyword")
    if _contains_any(question, IMPLEMENTATION_TERMS):
        signals.append("implementation_keyword")
    if _contains_any(question, MEMORY_TERMS):
        signals.append("memory_keyword")
    if _contains_any(question, ARCHITECTURE_TERMS):
        signals.append("architecture_keyword")
    if _contains_any(question, SYSTEM_TERMS):
        signals.append("system_keyword")
    if candidates:
        signals.append("indexed_symbol_candidates")
    if any(ref["cross_repo"] for ref in refs):
        signals.append("cross_repo_reference")
    if refs:
        signals.append("indexed_references")
    return signals


def route_request(conn: sqlite3.Connection, question: str, *, limit: int = 10) -> dict:
    question = question.strip()
    if not question:
        raise ValueError("question must be a non-empty string")
    limit = max(1, min(int(limit), 50))
    terms = _candidate_terms(question)
    candidates: list[dict] = []
    seen_ids = set()
    for term in terms:
        for item in search(conn, term, limit=5):
            key = (item["file"], item["qualified_name"], item["line"])
            if key not in seen_ids:
                seen_ids.add(key)
                candidates.append(item)
        if len(candidates) >= limit:
            break
    candidates = candidates[:limit]

    refs: list[dict] = []
    for candidate_name in dict.fromkeys(item["name"] for item in candidates):
        refs.extend(references(conn, candidate_name, limit=20))
    refs = refs[:100]

    signals = _signal_labels(question, candidates, refs)
    repos = {item["repo"] for item in candidates}
    subsystems = {item["subsystem"] for item in candidates}
    files = {item["file"] for item in candidates}
    cross_repo_refs = any(ref["cross_repo"] for ref in refs)
    ambiguous_refs = any(
        ref["resolution_status"] in {"ambiguous", "unresolved"} for ref in refs
    )

    if _contains_any(question, MEMORY_TERMS):
        intent = "memory"
    elif _contains_any(question, IMPACT_TERMS) or (
        _contains_any(question, IMPLEMENTATION_TERMS) and refs
    ):
        intent = "impact"
    elif _contains_any(question, IMPLEMENTATION_TERMS):
        intent = "implementation"
    else:
        intent = "locate"

    if intent == "memory":
        role = "module" if len(repos) <= 1 else "system"
    elif intent == "impact":
        if cross_repo_refs or len(subsystems) > 1 or _contains_any(
            question, ARCHITECTURE_TERMS
        ):
            role = "architect"
        elif refs:
            role = "system"
        elif len(repos) <= 1:
            role = "module"
        else:
            role = "system"
    elif intent == "implementation":
        role = "developer"
    elif _contains_any(question, ARCHITECTURE_TERMS):
        role = "architect"
    elif _contains_any(question, SYSTEM_TERMS):
        role = "system"
    elif len(files) == 1 and len(repos) == 1:
        role = "module"
    elif len(repos) <= 1:
        role = "system"
    else:
        role = "architect"

    confidence = 0.52
    if signals:
        confidence += min(0.18, 0.045 * len(signals))
    if candidates:
        confidence += 0.08
    if refs:
        confidence += 0.05
    if ambiguous_refs:
        confidence -= 0.08
    confidence = max(0.40, min(0.90, confidence))

    if intent == "locate":
        recommended_tools = ["atlas_search", "atlas_symbols", "atlas_file_context"]
    elif intent == "impact":
        recommended_tools = ["atlas_references", "atlas_impact"]
    elif intent == "implementation":
        recommended_tools = ["atlas_file_context", "atlas_scoped_search"]
    else:
        recommended_tools = ["atlas_memories", "atlas_file_context"]

    scope_candidates = []
    if intent == "implementation":
        for file_path in sorted(files):
            parts = file_path.rsplit("/", 1)
            if len(parts) == 2 and parts[0] and parts[0] != file_path:
                scope_candidates.append(parts[0])
    scope_candidates = list(dict.fromkeys(scope_candidates))[:5]

    warnings = []
    if not candidates:
        warnings.append("no indexed symbol candidates; verify index coverage")
    if ambiguous_refs:
        warnings.append("reference resolution is ambiguous or unresolved")
    if intent == "implementation" and not scope_candidates:
        warnings.append("cannot derive a narrow scope; manual path confirmation required")
    if intent == "impact" and not refs:
        warnings.append("no indexed references; impact may be incomplete")

    result = {
        "question": question,
        "intent": intent,
        "role": role,
        "confidence": round(confidence, 2),
        "signals": signals,
        "candidates": candidates,
        "reference_count": len(refs),
        "cross_repo_reference": cross_repo_refs,
        "recommended_tools": recommended_tools,
        "scope_candidates": scope_candidates,
        "warnings": warnings,
    }
    audit(
        conn,
        "router.request",
        {
            "intent": intent,
            "role": role,
            "confidence": result["confidence"],
            "candidate_count": len(candidates),
            "reference_count": len(refs),
            "signals": signals,
        },
        actor="deterministic-router",
    )
    conn.commit()
    return result


CASES_PATH = Path(__file__).with_name("data") / "router_cases.json"


def load_router_cases(path: str | Path | None = None) -> list[dict]:
    path = Path(path) if path else CASES_PATH
    with path.open("r", encoding="utf-8") as handle:
        cases = json.load(handle)
    if len(cases) < 10:
        raise ValueError("router validation requires at least 10 cases")
    for index, case in enumerate(cases):
        if "question" not in case or "expected_intent" not in case:
            raise ValueError(f"router case {index} requires question and expected_intent")
        if case["expected_intent"] not in {"locate", "impact", "implementation", "memory"}:
            raise ValueError(f"router case {index} has an invalid expected_intent")
    return cases


def evaluate_router(
    conn: sqlite3.Connection,
    *,
    cases: list[dict] | None = None,
    cases_path: str | Path | None = None,
    record: bool = True,
    limit: int = 10,
) -> dict:
    if cases is not None and cases_path is not None:
        raise ValueError("pass either cases or cases_path, not both")
    cases = load_router_cases(cases_path) if cases is None else cases
    failures = []
    intent_correct = 0
    role_evaluated = 0
    role_correct = 0
    for index, case in enumerate(cases):
        result = route_request(conn, case["question"], limit=limit)
        intent_match = result["intent"] == case["expected_intent"]
        role_match = None
        if intent_match:
            intent_correct += 1
        if "expected_role" in case:
            role_evaluated += 1
            role_match = result["role"] == case["expected_role"]
            if role_match:
                role_correct += 1
        if not intent_match or role_match is False:
            failures.append(
                {
                    "index": index,
                    "question": case["question"],
                    "expected_intent": case["expected_intent"],
                    "actual_intent": result["intent"],
                    "expected_role": case.get("expected_role"),
                    "actual_role": result["role"],
                }
            )
    total = len(cases)
    metrics = {
        "case_count": total,
        "intent_correct": intent_correct,
        "intent_accuracy": round(intent_correct / total, 4) if total else 0.0,
        "role_evaluated": role_evaluated,
        "role_correct": role_correct,
        "role_accuracy": round(role_correct / role_evaluated, 4) if role_evaluated else None,
        "failures": failures,
    }
    if record:
        audit(conn, "router.validation", metrics, actor="router-validator")
        conn.commit()
    return metrics
