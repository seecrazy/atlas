"""Query and scope operations over the ATLAS SQLite database."""

from __future__ import annotations

import json
import pathlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from .config import ProjectConfig
from .db import audit, utcnow


DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_SCOPE_TTL_SECONDS = 15 * 60


class ScopeError(ValueError):
    """Raised when a scope token is missing, invalid, expired, or out of range."""


def _limit(value: int | None) -> int:
    if value is None:
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, int(value)))


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _confidence(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    if value < 0.0 or value > 1.0:
        raise ValueError("min_confidence must be between 0.0 and 1.0")
    return value


def _latest_router_validation(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        """
        SELECT payload_json, created_at
        FROM audit_events
        WHERE event_type = 'router.validation'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except json.JSONDecodeError:
        return None
    return {**payload, "validated_at": row["created_at"]}


def _metric_p95(conn: sqlite3.Connection) -> float | None:
    rows = conn.execute(
        "SELECT duration_ms FROM run_metrics ORDER BY duration_ms"
    ).fetchall()
    if not rows:
        return None
    index = max(0, min(len(rows) - 1, int((len(rows) - 1) * 0.95)))
    return round(float(rows[index]["duration_ms"]), 3)


def health(conn: sqlite3.Connection) -> dict:
    def scalar(sql: str) -> int:
        row = conn.execute(sql).fetchone()
        return int(row[0] or 0)

    memory_rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM memories GROUP BY status"
    ).fetchall()
    diagnostic_rows = conn.execute(
        """
        SELECT level, COUNT(*) AS count
        FROM index_diagnostics
        GROUP BY level
        """
    ).fetchall()
    reference_rows = conn.execute(
        "SELECT resolution_status, COUNT(*) AS count FROM refs GROUP BY resolution_status"
    ).fetchall()
    languages = {
        row["language"]: int(row["count"])
        for row in conn.execute("SELECT language, COUNT(*) AS count FROM files GROUP BY language")
    }
    semantic_rows = conn.execute(
        "SELECT layer, COUNT(*) AS count FROM memories GROUP BY layer"
    ).fetchall()
    repos = conn.execute(
        """
        SELECT r.name, r.subsystem, r.path,
               COUNT(DISTINCT f.id) AS file_count,
               COUNT(DISTINCT s.id) AS symbol_count
        FROM repos r
        LEFT JOIN files f ON f.repo_id = r.id
        LEFT JOIN symbols s ON s.repo_id = r.id
        GROUP BY r.id, r.name, r.subsystem, r.path
        ORDER BY r.name
        """
    ).fetchall()
    return {
        "repos": [
            {
                "name": row["name"],
                "subsystem": row["subsystem"],
                "path": row["path"],
                "file_count": int(row["file_count"]),
                "symbol_count": int(row["symbol_count"]),
            }
            for row in repos
        ],
        "repo_count": len(repos),
        "file_count": scalar("SELECT COUNT(*) FROM files"),
        "symbol_count": scalar("SELECT COUNT(*) FROM symbols"),
        "reference_count": scalar("SELECT COUNT(*) FROM refs"),
        "references_by_resolution": {
            row["resolution_status"]: int(row["count"]) for row in reference_rows
        },
        "memory_count": scalar("SELECT COUNT(*) FROM memories"),
        "memory_by_status": {row["status"]: int(row["count"]) for row in memory_rows},
        "indexed_languages": languages,
        "diagnostics": {row["level"]: int(row["count"]) for row in diagnostic_rows},
        "diagnostic_count": scalar("SELECT COUNT(*) FROM index_diagnostics"),
        "router_validation": _latest_router_validation(conn),
        "exported_symbol_change_count": scalar(
            "SELECT COUNT(*) FROM exported_symbol_changes"
        ),
        "semantic_memory_count": scalar("SELECT COUNT(*) FROM memories WHERE layer = 'semantic'"),
        "memory_by_layer": {row["layer"]: int(row["count"]) for row in semantic_rows},
        "contract_count": scalar("SELECT COUNT(*) FROM contracts"),
        "architecture_doc_count": scalar("SELECT COUNT(*) FROM architecture_docs"),
        "pending_approval_count": scalar("SELECT COUNT(*) FROM approvals WHERE status = 'pending'"),
        "pending_patch_count": scalar("SELECT COUNT(*) FROM patches WHERE status = 'pending'"),
        "agent_run_count": scalar("SELECT COUNT(*) FROM agent_runs"),
        "failed_agent_run_count": scalar("SELECT COUNT(*) FROM agent_runs WHERE status = 'failed'"),
        "strategy_rule_count": scalar("SELECT COUNT(*) FROM strategy_rules"),
        "accepted_variant_count": scalar("SELECT COUNT(*) FROM evolution_variants WHERE status = 'accepted'"),
        "unresolved_reference_ratio": round(
            scalar("SELECT COUNT(*) FROM refs WHERE resolution_status = 'unresolved'")
            / max(1, scalar("SELECT COUNT(*) FROM refs")),
            4,
        ),
        "stale_memory_ratio": round(
            scalar("SELECT COUNT(*) FROM memories WHERE status = 'stale'")
            / max(1, scalar("SELECT COUNT(*) FROM memories")),
            4,
        ),
        "audit_event_count": scalar("SELECT COUNT(*) FROM audit_events"),
        "run_metric_count": scalar("SELECT COUNT(*) FROM run_metrics"),
        "failed_run_metric_count": scalar("SELECT COUNT(*) FROM run_metrics WHERE status = 'failed'"),
        "last_index_at": (
            conn.execute("SELECT MAX(last_indexed_at) AS value FROM index_state").fetchone()["value"]
        ),
        "last_daemon_cycle_at": (
            conn.execute("SELECT MAX(created_at) AS value FROM run_metrics WHERE event_type LIKE 'daemon.%'").fetchone()["value"]
        ),
        "average_metric_duration_ms": round(
            scalar("SELECT AVG(duration_ms) FROM run_metrics"), 3
        ),
        "p95_metric_duration_ms": _metric_p95(conn),
    }


def search(conn: sqlite3.Connection, query: str, limit: int | None = None) -> list[dict]:
    if not query.strip():
        return []
    pattern = _like(query.strip())
    rows = conn.execute(
        """
        SELECT
          s.name, s.qualified_name, s.kind, s.start_line, s.end_line, s.exported,
          r.name AS repo, r.subsystem, f.path, f.language
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        JOIN repos r ON r.id = s.repo_id
        WHERE s.name LIKE ? ESCAPE '\\'
           OR s.qualified_name LIKE ? ESCAPE '\\'
           OR f.path LIKE ? ESCAPE '\\'
        ORDER BY s.exported DESC, s.name, f.path, s.start_line
        LIMIT ?
        """,
        (pattern, pattern, pattern, _limit(limit)),
    ).fetchall()
    return [
        {
            "name": row["name"],
            "qualified_name": row["qualified_name"],
            "kind": row["kind"],
            "repo": row["repo"],
            "subsystem": row["subsystem"],
            "file": row["path"],
            "line": row["start_line"],
            "end_line": row["end_line"],
            "language": row["language"],
            "exported": bool(row["exported"]),
        }
        for row in rows
    ]


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w]+", query.lower())
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"*' for token in tokens)


def rebuild_search_index(conn: sqlite3.Connection) -> dict:
    conn.execute("DELETE FROM search_index")
    symbol_rows = conn.execute(
        """
        SELECT s.id, s.name, s.qualified_name, s.kind, s.exported, f.path,
               r.name AS repo, r.subsystem, f.mtime
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        JOIN repos r ON r.id = s.repo_id
        """
    ).fetchall()
    for row in symbol_rows:
        conn.execute(
            "INSERT INTO search_index(title, body, path, repo, subsystem, doc_type, doc_id, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                row["name"],
                f"{row['qualified_name']} {row['kind']} {'exported' if row['exported'] else ''}",
                row["path"], row["repo"], row["subsystem"], "symbol", row["id"],
                datetime.fromtimestamp(float(row["mtime"]), timezone.utc).isoformat(),
            ),
        )
    memory_rows = conn.execute(
        "SELECT id, memory_key, claim, updated_at FROM memories"
    ).fetchall()
    for row in memory_rows:
        conn.execute(
            "INSERT INTO search_index(title, body, path, repo, subsystem, doc_type, doc_id, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (row["memory_key"], row["claim"], "", "", "", "memory", row["id"], row["updated_at"]),
        )
    contract_rows = conn.execute("SELECT id, name, provider_repo, provider_subsystem, consumers_json, updated_at FROM contracts").fetchall()
    for row in contract_rows:
        conn.execute(
            "INSERT INTO search_index(title, body, path, repo, subsystem, doc_type, doc_id, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (row["name"], f"{row['provider_repo']} {row['consumers_json']}", "", row["provider_repo"], row["provider_subsystem"], "contract", row["id"], row["updated_at"]),
        )
    doc_rows = conn.execute("SELECT id, path, kind, title, summary, indexed_at FROM architecture_docs").fetchall()
    for row in doc_rows:
        conn.execute(
            "INSERT INTO search_index(title, body, path, repo, subsystem, doc_type, doc_id, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (row["title"], row["summary"], row["path"], "", "", row["kind"], row["id"], row["indexed_at"]),
        )
    conn.commit()
    return {"indexed": len(symbol_rows) + len(memory_rows) + len(contract_rows) + len(doc_rows)}


def hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int | None = None,
) -> list[dict]:
    if not query.strip():
        return []
    limit = _limit(limit)
    fts_query = _fts_query(query)
    if not fts_query:
        return search(conn, query, limit=limit)
    try:
        rows = conn.execute(
            """
            SELECT title, body, path, repo, subsystem, doc_type, doc_id, updated_at,
                   bm25(search_index) AS lexical_rank
            FROM search_index
            WHERE search_index MATCH ?
            ORDER BY lexical_rank
            LIMIT ?
            """,
            (fts_query, limit * 3),
        ).fetchall()
    except sqlite3.OperationalError:
        fallback = search(conn, query, limit=limit)
        return [
            {**item, "doc_type": "symbol", "score": 0.5, "freshness": "unknown"}
            for item in fallback
        ]

    now = datetime.now(timezone.utc)
    results = []
    for row in rows:
        lexical = max(0.0, -float(row["lexical_rank"]))
        normalized_lexical = min(1.0, lexical / (lexical + 3.0))
        type_weight = {"symbol": 1.0, "contract": 0.9, "memory": 0.7, "adr": 0.8}.get(row["doc_type"], 0.6)
        try:
            updated = datetime.fromisoformat(str(row["updated_at"]))
        except ValueError:
            updated = now
        age_days = max(0.0, (now - updated).total_seconds() / 86400)
        freshness = min(1.0, 1.0 / (1.0 + age_days / 30.0))
        score = round(0.55 * normalized_lexical + 0.3 * type_weight + 0.15 * freshness, 4)
        results.append(
            {
                "doc_type": row["doc_type"],
                "doc_id": row["doc_id"],
                "title": row["title"],
                "summary": row["body"][:500],
                "path": row["path"],
                "repo": row["repo"],
                "subsystem": row["subsystem"],
                "updated_at": row["updated_at"],
                "score": score,
                "freshness": round(freshness, 4),
                "lexical_score": round(normalized_lexical, 4),
            }
        )
    return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]


def symbols(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    file_path: str | None = None,
    repo: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    conditions = []
    params = []
    if query:
        conditions.append("(s.name LIKE ? ESCAPE '\\' OR s.qualified_name LIKE ? ESCAPE '\\')")
        pattern = _like(query)
        params.extend([pattern, pattern])
    if file_path:
        conditions.append("f.path LIKE ? ESCAPE '\\'")
        params.append(_like(file_path))
    if repo:
        conditions.append("r.name = ?")
        params.append(repo)
    sql = """
        SELECT
          s.name, s.qualified_name, s.kind, s.start_line, s.end_line, s.exported,
          r.name AS repo, r.subsystem, f.path, f.language
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        JOIN repos r ON r.id = s.repo_id
    """
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY f.path, s.start_line LIMIT ?"
    params.append(_limit(limit))
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "name": row["name"],
            "qualified_name": row["qualified_name"],
            "kind": row["kind"],
            "repo": row["repo"],
            "subsystem": row["subsystem"],
            "file": row["path"],
            "line": row["start_line"],
            "end_line": row["end_line"],
            "language": row["language"],
            "exported": bool(row["exported"]),
        }
        for row in rows
    ]


def references(
    conn: sqlite3.Connection,
    symbol_name: str,
    limit: int | None = None,
    *,
    min_confidence: float | None = None,
) -> list[dict]:
    if not symbol_name.strip():
        return []
    name = symbol_name.strip()
    threshold = _confidence(min_confidence)
    rows = conn.execute(
        """
        SELECT
          ref.target_name, ref.target_symbol, ref.resolution_status,
          ref.resolution_method, ref.confidence, ref.line, ref.kind,
          r.name AS repo, r.subsystem, f.path,
          r.id AS source_repo_id,
          sr.name AS target_repo,
          sr.id AS target_repo_id,
          sr.subsystem AS target_subsystem
        FROM refs ref
        JOIN files f ON f.id = ref.source_file_id
        JOIN repos r ON r.id = ref.source_repo_id
        LEFT JOIN symbols sym ON sym.id = ref.target_symbol_id
        LEFT JOIN repos sr ON sr.id = sym.repo_id
        WHERE (
           ref.target_name = ?
           OR ref.target_name LIKE '%.' || ?
           OR ref.target_name LIKE '%/' || ?
           OR (ref.target_symbol_id IS NOT NULL AND sym.name = ?)
           OR (ref.target_symbol_id IS NOT NULL AND sym.qualified_name = ?)
        )
           AND (? IS NULL OR ref.confidence >= ?)
        ORDER BY f.path, ref.line
        LIMIT ?
        """,
        (name, name, name, name, name, threshold, threshold, _limit(limit)),
    ).fetchall()
    return [
        {
            "target": row["target_name"],
            "resolved_symbol": row["target_symbol"],
            "resolution_status": row["resolution_status"],
            "resolution_method": row["resolution_method"],
            "confidence": row["confidence"],
            "source_repo": row["repo"],
            "subsystem": row["subsystem"],
            "target_repo": row["target_repo"],
            "target_subsystem": row["target_subsystem"],
            "cross_repo": (
                row["target_repo"] is not None
                and row["source_repo_id"] != row["target_repo_id"]
            ),
            "source_file": row["path"],
            "line": row["line"],
            "kind": row["kind"],
        }
        for row in rows
    ]


def impact(
    conn: sqlite3.Connection,
    symbol_name: str,
    limit: int | None = None,
    *,
    min_confidence: float | None = None,
) -> dict:
    all_refs = references(conn, symbol_name, limit=MAX_LIMIT)
    threshold = _confidence(min_confidence)
    refs = all_refs if threshold is None else [
        ref for ref in all_refs if ref["confidence"] >= threshold
    ]
    by_repo: dict[str, list[dict]] = {}
    for ref in refs:
        by_repo.setdefault(ref["source_repo"], []).append(ref)
    by_resolution: dict[str, int] = {}
    for ref in all_refs:
        by_resolution[ref["resolution_status"]] = by_resolution.get(ref["resolution_status"], 0) + 1
    return {
        "symbol": symbol_name,
        "min_confidence": threshold,
        "reference_count": len(refs),
        "filtered_count": len(all_refs) - len(refs),
        "references_by_resolution": by_resolution,
        "affected_repos": sorted(by_repo),
        "references": refs,
    }


def impact_chain(
    conn: sqlite3.Connection,
    symbol_name: str,
    *,
    limit: int | None = None,
    min_confidence: float | None = None,
) -> dict:
    """Return direct references plus one module-level upstream hop.

    The first hop is a direct reference to the target symbol. The second hop is
    a reference to any symbol exported from a first-hop source file. This is an
    import/use graph, not a precise function-call graph.
    """
    threshold = _confidence(min_confidence)
    limit = _limit(limit)
    base = impact(conn, symbol_name, limit=limit, min_confidence=threshold)
    direct = base["references"]

    direct_file_ids: dict[str, int] = {}
    for ref in direct:
        row = conn.execute(
            "SELECT id FROM files WHERE path = ?", (ref["source_file"],)
        ).fetchone()
        if row is not None:
            direct_file_ids[ref["source_file"]] = int(row["id"])

    second_hop: list[dict] = []
    if direct_file_ids:
        file_ids = list(direct_file_ids.values())
        placeholders = ",".join("?" for _ in file_ids)
        rows = conn.execute(
            f"""
            SELECT
              ref2.target_name, ref2.target_symbol, ref2.resolution_status,
              ref2.resolution_method, ref2.confidence, ref2.line, ref2.kind,
              r2.name AS repo, r2.subsystem, sf2.path AS source_file,
              tf2.path AS first_hop_file,
              ts2.name AS first_hop_symbol
            FROM refs ref2
            JOIN symbols ts2 ON ts2.id = ref2.target_symbol_id
            JOIN files tf2 ON tf2.id = ts2.file_id
            JOIN files sf2 ON sf2.id = ref2.source_file_id
            JOIN repos r2 ON r2.id = ref2.source_repo_id
            WHERE tf2.id IN ({placeholders})
              AND sf2.id != tf2.id
              AND (? IS NULL OR ref2.confidence >= ?)
            ORDER BY sf2.path, ref2.line
            LIMIT ?
            """,
            (*file_ids, threshold, threshold, limit),
        ).fetchall()
        seen_ids: set[tuple[str, str, int, str]] = set()
        for row in rows:
            key = (
                row["source_file"],
                row["target_name"],
                row["line"],
                row["first_hop_file"],
            )
            if key in seen_ids:
                continue
            seen_ids.add(key)
            second_hop.append(
                {
                    "target": row["target_name"],
                    "resolved_symbol": row["target_symbol"],
                    "resolution_status": row["resolution_status"],
                    "resolution_method": row["resolution_method"],
                    "confidence": row["confidence"],
                    "source_repo": row["repo"],
                    "subsystem": row["subsystem"],
                    "source_file": row["source_file"],
                    "line": row["line"],
                    "kind": row["kind"],
                    "first_hop_file": row["first_hop_file"],
                    "first_hop_symbol": row["first_hop_symbol"],
                }
            )

    repos = sorted(
        {ref["source_repo"] for ref in direct}
        | {ref["source_repo"] for ref in second_hop}
    )
    return {
        "symbol": symbol_name,
        "min_confidence": threshold,
        "max_hops": 2,
        "direct_reference_count": len(direct),
        "second_hop_reference_count": len(second_hop),
        "affected_repos": repos,
        "first_hop": direct,
        "second_hop": second_hop,
        "warnings": [] if direct else ["no direct references; chain is incomplete"],
    }


def file_context(conn: sqlite3.Connection, file_path: str, limit: int | None = None) -> dict:
    file_row = conn.execute(
        """
        SELECT f.id, f.path, f.language, f.module_name, f.status,
               r.name AS repo, r.subsystem
        FROM files f
        JOIN repos r ON r.id = f.repo_id
        WHERE f.path = ?
        """,
        (file_path,),
    ).fetchone()
    if file_row is None:
        raise FileNotFoundError(f"indexed file not found: {file_path}")
    symbol_rows = conn.execute(
        """
        SELECT name, qualified_name, kind, start_line, end_line, exported
        FROM symbols WHERE file_id = ?
        ORDER BY start_line LIMIT ?
        """,
        (file_row["id"], _limit(limit)),
    ).fetchall()
    import_rows = conn.execute(
        """
        SELECT target_name, target_symbol, resolution_status,
               resolution_method, confidence, line, kind
        FROM refs WHERE source_file_id = ? ORDER BY line LIMIT ?
        """,
        (file_row["id"], _limit(limit)),
    ).fetchall()
    return {
        "file": file_row["path"],
        "repo": file_row["repo"],
        "subsystem": row_subsystem(file_row),
        "language": file_row["language"],
        "module": file_row["module_name"],
        "status": file_row["status"],
        "symbols": [
            {
                "name": row["name"],
                "qualified_name": row["qualified_name"],
                "kind": row["kind"],
                "line": row["start_line"],
                "end_line": row["end_line"],
                "exported": bool(row["exported"]),
            }
            for row in symbol_rows
        ],
        "imports": [
            {
                "target": row["target_name"],
                "resolved_symbol": row["target_symbol"],
                "resolution_status": row["resolution_status"],
                "confidence": row["confidence"],
                "line": row["line"],
                "kind": row["kind"],
            }
            for row in import_rows
        ],
    }


def row_subsystem(row: sqlite3.Row) -> str:
    return row["subsystem"]


def _validate_scope_paths(
    conn: sqlite3.Connection,
    config: ProjectConfig,
    paths: list[str],
) -> list[str]:
    normalized: list[str] = []
    for value in paths:
        if not isinstance(value, str) or not value.strip():
            raise ScopeError("scope paths must be non-empty strings")
        clean = pathlib.PurePosixPath(value.strip().strip("/"))
        if not clean.parts or clean.is_absolute() or ".." in clean.parts:
            raise ScopeError(f"invalid scope path: {value}")
        normalized.append(clean.as_posix())
    if not normalized:
        raise ScopeError("at least one scope path is required")
    # Phase 1 scope paths are project-relative stable paths such as
    # payment-core/src/retry. This prevents creating a token rooted outside the
    # configured repository graph.
    repo_names = {row["name"] for row in conn.execute("SELECT name FROM repos")}
    for path in normalized:
        if path.split("/", 1)[0] not in repo_names:
            raise ScopeError(f"scope path is not rooted at a registered repo: {path}")
    return normalized


def create_scope(
    conn: sqlite3.Connection,
    config: ProjectConfig,
    paths: list[str],
    *,
    ttl_seconds: int = DEFAULT_SCOPE_TTL_SECONDS,
    created_by: str = "atlas-cli",
) -> dict:
    if ttl_seconds <= 0 or ttl_seconds > 24 * 60 * 60:
        raise ScopeError("scope ttl must be between 1 second and 24 hours")
    validated = _validate_scope_paths(conn, config, paths)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    conn.execute(
        "INSERT INTO scope_tokens(token, paths_json, created_at, expires_at, created_by) VALUES(?,?,?,?,?)",
        (token, json.dumps(validated, ensure_ascii=False), utcnow(), expires.isoformat(), created_by),
    )
    audit(
        conn,
        "scope.created",
        {"paths": validated, "expires_at": expires.isoformat(), "created_by": created_by},
        actor=created_by,
    )
    conn.commit()
    return {"token": token, "paths": validated, "expires_at": expires.isoformat()}


def _active_scope(conn: sqlite3.Connection, token: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM scope_tokens WHERE token = ?", (token,)).fetchone()
    if row is None:
        raise ScopeError("invalid scope token")
    if row["status"] != "active":
        raise ScopeError(f"scope token is not active: {row['status']}")
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
    except ValueError as exc:
        raise ScopeError("scope token has an invalid expiration") from exc
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        conn.execute(
            "UPDATE scope_tokens SET status = 'expired' WHERE id = ? AND status = 'active'",
            (row["id"],),
        )
        conn.commit()
        raise ScopeError("scope token expired")
    return row


def _within_scope(path: str, scope_paths: list[str]) -> bool:
    parts = pathlib.PurePosixPath(path).parts
    return any(
        path == scope or path.startswith(scope + "/")
        for scope in scope_paths
    )


def scoped_search(
    conn: sqlite3.Connection,
    token: str,
    query: str,
    limit: int | None = None,
    *,
    actor: str = "scoped-agent",
) -> list[dict]:
    scope = _active_scope(conn, token)
    scope_paths = json.loads(scope["paths_json"])
    results = search(conn, query, limit=limit)
    scoped = [item for item in results if _within_scope(item["file"], scope_paths)]
    now = utcnow()
    conn.execute(
        "UPDATE scope_tokens SET used_at = COALESCE(used_at, ?) WHERE id = ?",
        (now, scope["id"]),
    )
    audit(
        conn,
        "scope.used",
        {"query": query, "result_count": len(scoped), "paths": scope_paths},
        actor=actor,
    )
    conn.commit()
    return scoped


def revoke_scope(conn: sqlite3.Connection, token: str) -> bool:
    cursor = conn.execute(
        "UPDATE scope_tokens SET status = 'revoked' WHERE token = ? AND status = 'active'",
        (token,),
    )
    changed = bool(cursor.rowcount)
    if changed:
        audit(conn, "scope.revoked", {"token_hash": secrets.token_hex(8)})
        conn.commit()
    return changed


def _physical_file_path(row: sqlite3.Row) -> pathlib.Path:
    stable_path = str(row["path"])
    repo_name = str(row["repo_name"])
    relative = stable_path[len(repo_name) + 1:] if stable_path.startswith(repo_name + "/") else stable_path
    return pathlib.Path(row["repo_path"]) / relative


def _ensure_physical_path_inside_repo(physical: pathlib.Path, repo_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(repo_path).resolve()
    resolved = physical.resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ScopeError(
            f"physical path escapes repository: {physical} -> {resolved}"
        ) from exc
    if any(part.is_symlink() for part in physical.parents):
        raise ScopeError(f"symlinked path is not allowed: {physical}")


def read_scoped_file(
    conn: sqlite3.Connection,
    token: str,
    file_path: str,
    *,
    max_chars: int = 12000,
    actor: str = "developer-agent",
) -> dict:
    """Read a physical file only after server-side scope validation."""
    scope = _active_scope(conn, token)
    scope_paths = json.loads(scope["paths_json"])
    if not _within_scope(file_path, scope_paths):
        raise ScopeError(f"path is outside scope token: {file_path}")
    row = conn.execute(
        """
        SELECT f.id, f.path, f.language, r.path AS repo_path, r.name AS repo_name
        FROM files f
        JOIN repos r ON r.id = f.repo_id
        WHERE f.path = ?
        """,
        (file_path,),
    ).fetchone()
    if row is None:
        raise FileNotFoundError(f"indexed file not found: {file_path}")
    physical = _physical_file_path(row)
    _ensure_physical_path_inside_repo(physical, pathlib.Path(row["repo_path"]))
    if not physical.is_file():
        raise FileNotFoundError(f"physical file missing: {physical}")
    try:
        text = physical.read_text(encoding="utf-8")[:max_chars]
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read file {file_path}: {exc}") from exc
    truncated = len(text) >= max_chars
    audit(conn, "scope.file_read", {"file": file_path, "truncated": truncated}, actor=actor)
    conn.commit()
    return {
        "file": file_path,
        "language": row["language"],
        "content": text,
        "truncated": truncated,
    }


def memories(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    layer: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    conditions = []
    params: list[object] = []
    if status is not None:
        if status not in {"valid", "stale", "invalid"}:
            raise ValueError("memory status must be valid, stale, or invalid")
        conditions.append("m.status = ?")
        params.append(status)
    if layer is not None:
        if layer not in {"structural", "semantic", "behavioral"}:
            raise ValueError("memory layer must be structural, semantic, or behavioral")
        conditions.append("m.layer = ?")
        params.append(layer)
    sql = "SELECT * FROM memories m"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY m.updated_at DESC LIMIT ?"
    params.append(_limit(limit))
    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        evidence = conn.execute(
            """
            SELECT e.start_line, e.end_line, f.path, f.language,
                   r.name AS repo, r.subsystem
            FROM memory_evidence e
            JOIN files f ON f.id = e.file_id
            JOIN repos r ON r.id = f.repo_id
            WHERE e.memory_id = ?
            ORDER BY f.path, e.start_line
            """,
            (row["id"],),
        ).fetchall()
        result.append(
            {
                "id": row["id"],
                "memory_key": row["memory_key"],
                "layer": row["layer"],
                "claim": row["claim"],
                "status": row["status"],
                "confidence": row["confidence"],
                "utility_score": row["utility_score"],
                "updated_at": row["updated_at"],
                "evidence": [
                    {
                        "repo": item["repo"],
                        "subsystem": item["subsystem"],
                        "file": item["path"],
                        "start_line": item["start_line"],
                        "end_line": item["end_line"],
                        "language": item["language"],
                    }
                    for item in evidence
                ],
            }
        )
    return result


def review_memory(
    conn: sqlite3.Connection,
    memory_id: int,
    decision: str,
    *,
    reviewer: str,
    reason: str,
) -> dict:
    if decision not in {"valid", "invalid"}:
        raise ValueError("memory review decision must be valid or invalid")
    if not reviewer.strip():
        raise ValueError("reviewer must be a non-empty string")
    if not reason.strip():
        raise ValueError("reason must be a non-empty string")
    row = conn.execute("SELECT id FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
    if row is None:
        raise FileNotFoundError(f"memory not found: {memory_id}")
    now = utcnow()
    conn.execute(
        "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
        (decision, now, int(memory_id)),
    )
    conn.execute(
        """
        INSERT INTO memory_reviews(memory_id, reviewer, decision, reason, created_at)
        VALUES(?,?,?,?,?)
        """,
        (int(memory_id), reviewer.strip(), decision, reason.strip(), now),
    )
    audit(
        conn,
        "memory.reviewed",
        {
            "memory_id": int(memory_id),
            "decision": decision,
            "reason": reason.strip(),
        },
        actor=reviewer.strip(),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
    return {
        "id": updated["id"],
        "memory_key": updated["memory_key"],
        "status": updated["status"],
        "updated_at": updated["updated_at"],
    }

