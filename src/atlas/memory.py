"""Semantic memory generation, drift review, lessons, and utility tracking."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sqlite3
import subprocess
from collections import defaultdict

from .config import ProjectConfig
from .db import audit, connect, utcnow
from .llm import LLMProvider, LLMUnavailable
from .kernel import Budget


def _memory_key(scope_type: str, scope_key: str) -> str:
    raw = f"{scope_type}:{scope_key}"
    return f"mem-{scope_type}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _upsert_memory(
    conn: sqlite3.Connection,
    *,
    scope_type: str,
    scope_key: str,
    claim: str,
    evidence: list[dict],
    confidence: float,
    source: str = "semantic-generator",
) -> str:
    key = _memory_key(scope_type, scope_key)
    now = utcnow()
    conn.execute(
        """
        INSERT INTO memories(
          memory_key, layer, claim, source, confidence, utility_score,
          status, created_at, updated_at
        ) VALUES(?, 'semantic', ?, ?, 0.72, 0.5, 'valid', ?, ?)
        ON CONFLICT(memory_key) DO UPDATE SET
          claim=excluded.claim,
          confidence=excluded.confidence,
          status='valid',
          updated_at=excluded.updated_at
        """,
        (key, claim, source, now, now),
    )
    memory_id = conn.execute("SELECT id FROM memories WHERE memory_key = ?", (key,)).fetchone()["id"]
    conn.execute("DELETE FROM memory_evidence WHERE memory_id = ?", (memory_id,))
    for item in evidence:
        conn.execute(
            "INSERT OR IGNORE INTO memory_evidence(memory_id, file_id, start_line, end_line) VALUES(?,?,?,?)",
            (memory_id, item["file_id"], item["start_line"], item["end_line"]),
        )
    return key


def _file_rows(conn: sqlite3.Connection, scope: str | None = None) -> list[sqlite3.Row]:
    if scope:
        return conn.execute(
            """
            SELECT f.id, f.repo_id, f.path, f.language, f.module_name, r.name AS repo, r.subsystem
            FROM files f JOIN repos r ON r.id = f.repo_id
            WHERE r.name = ? OR r.subsystem = ? OR f.path LIKE ?
            ORDER BY f.path
            """,
            (scope, scope, f"%{scope}%"),
        ).fetchall()
    return conn.execute(
        """
        SELECT f.id, f.repo_id, f.path, f.language, f.module_name, r.name AS repo, r.subsystem
        FROM files f JOIN repos r ON r.id = f.repo_id
        ORDER BY f.path
        """
    ).fetchall()


def generate_semantic_memories(
    conn: sqlite3.Connection,
    config: ProjectConfig,
    *,
    scope: str | None = None,
    use_llm: bool = False,
) -> dict:
    budget = Budget.from_config(config)
    provider = LLMProvider.from_config(config)
    generated = []
    now = utcnow()
    file_rows = _file_rows(conn, scope)
    budget.spend(calls=1)
    for row in file_rows:
        budget.spend(calls=1)
        symbol_rows = conn.execute(
            """
            SELECT name, qualified_name, kind, start_line, end_line, exported
            FROM symbols WHERE file_id = ? ORDER BY start_line LIMIT 50
            """,
            (row["id"],),
        ).fetchall()
        import_rows = conn.execute(
            "SELECT target_name FROM refs WHERE source_file_id = ? AND kind = 'import' LIMIT 50",
            (row["id"],),
        ).fetchall()
        exported = [item["name"] for item in symbol_rows if item["exported"]]
        imports = [item["target_name"] for item in import_rows]
        claim = (
            f"{row['path']} defines {len(symbol_rows)} indexed symbols "
            f"({len(exported)} exported) and imports {len(imports)} targets. "
            f"Primary exports: {', '.join(exported[:8]) or 'none'}."
        )
        if use_llm and provider.available:
            prompt = json.dumps(
                {
                    "file": row["path"],
                    "symbols": [dict(item) for item in symbol_rows],
                    "imports": imports,
                },
                ensure_ascii=False,
            )
            try:
                budget.spend(calls=1, tokens=min(budget.max_tokens, len(prompt) // 3 + 100))
                claim = provider.complete(
                    prompt,
                    system=(
                        "You are ATLAS semantic indexer. Produce one factual claim "
                        "about code structure. Never invent behavior. Use English or Chinese "
                        "consistently."
                    ),
                )
                provider_name = "openai-compatible"
            except LLMUnavailable:
                provider_name = "deterministic-fallback"
        else:
            provider_name = "deterministic"
        memory_key = _upsert_memory(
            conn,
            scope_type="file",
            scope_key=row["path"],
            claim=claim,
            evidence=[{
                "file_id": row["id"],
                "start_line": symbol_rows[0]["start_line"] if symbol_rows else 1,
                "end_line": symbol_rows[-1]["end_line"] if symbol_rows else 1,
            }],
            confidence=0.72,
            source=provider_name,
        )
        generation_key = f"file:{hashlib.sha256(row['path'].encode()).hexdigest()[:16]}"
        conn.execute(
            """
            INSERT INTO semantic_generations(
              generation_key, scope_type, scope_key, provider, status, memory_key, created_at
            ) VALUES(?, 'file', ?, ?, 'completed', ?, ?)
            ON CONFLICT(generation_key) DO UPDATE SET
              status='completed', memory_key=excluded.memory_key, provider=excluded.provider
            """,
            (generation_key, row["path"], provider_name, memory_key, now),
        )
        generated.append({"scope": row["path"], "memory_key": memory_key, "provider": provider_name})

    grouped_repo: dict[str, list[sqlite3.Row]] = defaultdict(list)
    grouped_subsystem: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in file_rows:
        grouped_repo[row["repo"]].append(row)
        grouped_subsystem[row["subsystem"]].append(row)
    for repo, rows in grouped_repo.items():
        budget.spend(calls=1)
        paths = [item["path"] for item in rows]
        claim = f"Repository {repo} has {len(rows)} indexed files across {len(set(paths))} paths."
        memory_key = _upsert_memory(
            conn,
            scope_type="repo",
            scope_key=repo,
            claim=claim,
            evidence=[{"file_id": rows[0]["id"], "start_line": 1, "end_line": 1}],
            confidence=0.78,
        )
        generated.append({"scope": repo, "memory_key": memory_key, "provider": "deterministic"})
    for subsystem, rows in grouped_subsystem.items():
        budget.spend(calls=1)
        repos = sorted({item["repo"] for item in rows})
        claim = f"Subsystem {subsystem} spans repositories {', '.join(repos)} with {len(rows)} indexed files."
        memory_key = _upsert_memory(
            conn,
            scope_type="subsystem",
            scope_key=subsystem,
            claim=claim,
            evidence=[{"file_id": rows[0]["id"], "start_line": 1, "end_line": 1}],
            confidence=0.82,
        )
        generated.append({"scope": subsystem, "memory_key": memory_key, "provider": "deterministic"})

    audit(conn, "memory.generated", {"count": len(generated), "scope": scope})
    conn.commit()
    return {"generated": generated, "count": len(generated), "budget": budget.usage()}


def _physical_path(row: sqlite3.Row) -> pathlib.Path:
    stable_path = str(row["path"])
    repo_name = str(row["repo"])
    prefix = repo_name + "/"
    relative = stable_path[len(prefix):] if stable_path.startswith(prefix) else stable_path
    return pathlib.Path(row["repo_path"]) / relative


def auto_review_stale_memories(conn: sqlite3.Connection, *, limit: int = 100) -> dict:
    rows = conn.execute(
        """
        SELECT m.id, m.claim, f.path, r.path AS repo_path, r.name AS repo
        FROM memories m
        JOIN memory_evidence e ON e.memory_id = m.id
        JOIN files f ON f.id = e.file_id
        JOIN repos r ON r.id = f.repo_id
        WHERE m.status = 'stale'
        ORDER BY m.updated_at
        LIMIT ?
        """,
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    decisions = []
    for row in rows:
        path = _physical_path(row)
        valid = False
        reason = "evidence file missing"
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            terms = [
                term
                for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", row["claim"])
                if term.lower() not in {"the", "and", "for", "with", "from"}
            ][:8]
            matches = [term for term in terms if term in text]
            valid = bool(matches)
            reason = f"{len(matches)}/{len(terms)} claim terms found in evidence"
        status = "valid" if valid else "invalid"
        conn.execute(
            "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
            (status, utcnow(), row["id"]),
        )
        conn.execute(
            """
            INSERT INTO memory_reviews(memory_id, reviewer, decision, reason, created_at)
            VALUES(?, 'auto-reviewer', ?, ?, ?)
            """,
            (row["id"], status, reason, utcnow()),
        )
        decisions.append({"memory_id": row["id"], "status": status, "reason": reason})
    audit(conn, "memory.auto_reviewed", {"count": len(decisions)})
    conn.commit()
    return {"reviewed": decisions, "count": len(decisions)}


def extract_revert_lessons(conn: sqlite3.Connection, config: ProjectConfig, *, limit: int = 20) -> dict:
    lessons = []
    for repo_row in conn.execute("SELECT id, name, path FROM repos ORDER BY name").fetchall():
        try:
            result = subprocess.run(
                [
                    "git", "-C", repo_row["path"], "log",
                    "--grep=Revert", "--pretty=format:%H%x1f%s%x1f%b%x1e",
                    "-n", str(limit),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        records = [item for item in result.stdout.split("\x1e") if item.strip()]
        evidence_row = conn.execute(
            "SELECT id FROM files WHERE repo_id = ? ORDER BY path LIMIT 1",
            (repo_row["id"],),
        ).fetchone()
        if evidence_row is None:
            continue
        for record in records:
            fields = record.strip().split("\x1f")
            if len(fields) < 2:
                continue
            commit, subject = fields[0], fields[1]
            body = fields[2] if len(fields) > 2 else ""
            claim = f"Revert {commit[:10]}: {subject}. Lesson evidence: {body.strip()[:300]}"
            memory_key = _upsert_memory(
                conn,
                scope_type="behavior",
                scope_key=f"{repo_row['name']}:{commit}",
                claim=claim,
                evidence=[{"file_id": evidence_row["id"], "start_line": 1, "end_line": 1}],
                confidence=0.65,
                source="git-revert-detector",
            )
            lessons.append({"repo": repo_row["name"], "commit": commit, "memory_key": memory_key})
    audit(conn, "memory.lessons_extracted", {"count": len(lessons)})
    conn.commit()
    return {"lessons": lessons, "count": len(lessons)}


def touch_memory(conn: sqlite3.Connection, memory_key: str, *, utility_delta: float = 0.01) -> dict:
    row = conn.execute("SELECT id, utility_score FROM memories WHERE memory_key = ?", (memory_key,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"memory not found: {memory_key}")
    new_score = min(1.0, max(0.0, float(row["utility_score"]) + float(utility_delta)))
    conn.execute(
        "UPDATE memories SET utility_score = ?, updated_at = ? WHERE id = ?",
        (new_score, utcnow(), row["id"]),
    )
    conn.commit()
    return {"memory_key": memory_key, "utility_score": new_score}
