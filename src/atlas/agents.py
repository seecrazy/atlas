"""Four-layer role agents with optional LLM reasoning and kernel budgets."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .config import ProjectConfig
from .context import developer_context, module_context, system_context
from .db import audit, utcnow
from .kernel import Budget, private_score, verify_finding
from .llm import LLMProvider, LLMUnavailable
from .router import route_request
from .store import create_scope, read_scoped_file
from .synthesis import converge_findings


ROLE_ORDER = ["architect", "system", "module", "developer"]
ROLE_DESCRIPTIONS = {
    "architect": "You reason only about subsystems, contracts, and cross-repo impact.",
    "system": "You reason only about repositories, deployment, and repo-level boundaries.",
    "module": "You reason only about modules, symbols, imports, and inbound dependencies.",
    "developer": "You reason about a scoped implementation and return structural findings.",
}


def _run_key(question: str) -> str:
    return hashlib.sha256(f"{question}:{utcnow()}".encode("utf-8")).hexdigest()[:24]


def _llm_claim(
    provider: LLMProvider,
    role: str,
    payload: dict,
    budget: Budget,
) -> str | None:
    if not provider.available:
        return None
    try:
        prompt = json.dumps({"role": role, "context": payload}, ensure_ascii=False)
        budget.spend(calls=1, tokens=min(budget.max_tokens, len(prompt) // 3 + 100))
        return provider.complete(
            prompt,
            system=ROLE_DESCRIPTIONS[role] + " Return one factual claim with no speculation.",
        )
    except LLMUnavailable:
        return None


def run_layered_agents(
    conn: sqlite3.Connection,
    config: ProjectConfig,
    question: str,
    *,
    use_llm: bool = True,
    min_confidence: float = 0.65,
    limit: int = 50,
) -> dict:
    question = question.strip()
    if not question:
        raise ValueError("question must be non-empty")
    run_key = _run_key(question)
    budget = Budget.from_config(config)
    conn.execute(
        "INSERT INTO agent_runs(run_key, question, status, budget_json, created_at) VALUES(?,?,?,?,?)",
        (run_key, question, "running", json.dumps(config.budgets, sort_keys=True), utcnow()),
    )
    conn.commit()
    findings = []
    contexts = {}
    warnings = []
    try:
        budget.spend(calls=1)
        route = route_request(conn, question, limit=min(limit, 10))
        contexts["route"] = route
        candidates = route.get("candidates", [])
        if not candidates:
            warnings.append("router found no indexed candidates")
            candidate = None
        else:
            candidate = candidates[0]

        budget.spend(calls=1)
        contexts["architect"] = system_context(conn)
        claim = _llm_claim(
            LLMProvider.from_config(config),
            "architect",
            contexts["architect"],
            budget,
        ) if use_llm else None
        claim = claim or (
            f"System scope contains {len(contexts['architect']['repos'])} repos and "
            f"{len(contexts['architect']['cross_repo_edges'])} cross-repo edges."
        )
        evidence = [{"file": candidate["file"], "line": candidate["line"]}] if candidate else []
        if evidence:
            findings.append({
                "role": "architect", "claim": claim, "confidence": 0.75,
                "evidence": evidence,
            })

        if candidate:
            budget.spend(calls=1)
            contexts["system"] = system_context(conn, repo=candidate["repo"])
            claim = _llm_claim(
                LLMProvider.from_config(config),
                "system",
                contexts["system"],
                budget,
            ) if use_llm else None
            claim = claim or (
                f"Repository {candidate['repo']} is the nearest indexed system boundary."
            )
            findings.append({
                "role": "system", "claim": claim, "confidence": 0.72,
                "evidence": [{"file": candidate["file"], "line": candidate["line"]}],
            })

            budget.spend(calls=1)
            contexts["module"] = module_context(conn, candidate["file"])
            claim = _llm_claim(
                LLMProvider.from_config(config),
                "module",
                contexts["module"],
                budget,
            ) if use_llm else None
            claim = claim or (
                f"Module context has {len(contexts['module']['symbols'])} symbols and "
                f"{len(contexts['module']['inbound_references'])} inbound references."
            )
            findings.append({
                "role": "module", "claim": claim, "confidence": 0.76,
                "evidence": [{"file": candidate["file"], "line": candidate["line"]}],
            })

            budget.spend(calls=1)
            scope = create_scope(
                conn,
                config,
                [candidate["file"].rsplit("/", 1)[0]],
                created_by="layered-orchestrator",
            )
            contexts["developer"] = developer_context(
                conn,
                candidate["file"],
                symbol=candidate.get("name"),
            )
            source = read_scoped_file(
                conn,
                scope["token"],
                candidate["file"],
                max_chars=6000,
            )
            contexts["developer_source"] = {
                "file": source["file"],
                "truncated": source["truncated"],
                "language": source["language"],
            }
            claim = _llm_claim(
                LLMProvider.from_config(config),
                "developer",
                {"context": contexts["developer"], "source_preview": source["content"][:3000]},
                budget,
            ) if use_llm else None
            claim = claim or (
                f"Scoped target {candidate.get('name')} is present in {candidate['file']} "
                f"at line {candidate['line']}."
            )
            findings.append({
                "role": "developer", "claim": claim, "confidence": 0.74,
                "evidence": [{"file": candidate["file"], "line": candidate["line"]}],
                "recommended_actions": ["review scoped source before making changes"],
            })

        if findings:
            for finding in findings:
                verification = verify_finding(conn, finding)
                finding["kernel_verification"] = verification
            convergence = converge_findings(
                conn,
                question,
                findings,
                route=route,
                impact_symbol=candidate.get("name") if route["intent"] == "impact" and candidate else None,
                min_confidence=min_confidence,
                limit=limit,
            )
        else:
            convergence = None

        result = {
            "run_key": run_key,
            "question": question,
            "route": route,
            "contexts": contexts,
            "workers": {"findings": findings},
            "convergence": convergence,
            "warnings": warnings,
            "budget": budget.usage(),
            "private_score": private_score({"ordered_findings": findings}),
        }
        conn.execute(
            """
            UPDATE agent_runs
               SET status='completed', usage_json=?, result_json=?, completed_at=?
             WHERE run_key=?
            """,
            (
                json.dumps(budget.usage(), sort_keys=True),
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                utcnow(),
                run_key,
            ),
        )
        audit(conn, "agent.run.completed", {"run_key": run_key, "score": result["private_score"]})
        conn.commit()
        return result
    except Exception as exc:
        conn.execute(
            """
            UPDATE agent_runs SET status='failed', usage_json=?, error=?, completed_at=?
             WHERE run_key=?
            """,
            (json.dumps(budget.usage(), sort_keys=True), str(exc), utcnow(), run_key),
        )
        audit(conn, "agent.run.failed", {"run_key": run_key, "error": str(exc)})
        conn.commit()
        raise
