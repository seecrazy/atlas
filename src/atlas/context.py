"""Structured context packets for system, module, and developer layers."""

from __future__ import annotations

import sqlite3


MAX_CONTEXT_ITEMS = 200


def _limit(value: int | None) -> int:
    if value is None:
        return 50
    return max(1, min(MAX_CONTEXT_ITEMS, int(value)))


def _require_nonempty(rows: list[sqlite3.Row], message: str) -> list[sqlite3.Row]:
    if not rows:
        raise FileNotFoundError(message)
    return rows


def system_context(
    conn: sqlite3.Connection,
    *,
    subsystem: str | None = None,
    repo: str | None = None,
    limit: int | None = None,
) -> dict:
    """Build a system-engineer packet: repos, coverage, and cross-repo edges."""
    limit = _limit(limit)
    conditions = []
    params: list[object] = []
    if subsystem is not None:
        conditions.append("r.subsystem = ?")
        params.append(subsystem)
    if repo is not None:
        conditions.append("r.name = ?")
        params.append(repo)
    repo_where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    repo_rows = conn.execute(
        f"""
        SELECT
          r.id, r.name, r.subsystem, r.path,
          COUNT(DISTINCT f.id) AS file_count,
          COUNT(DISTINCT s.id) AS symbol_count,
          COUNT(DISTINCT CASE WHEN s.exported = 1 THEN s.id END) AS exported_symbol_count,
          COUNT(DISTINCT ref.id) AS outgoing_reference_count
        FROM repos r
        LEFT JOIN files f ON f.repo_id = r.id
        LEFT JOIN symbols s ON s.repo_id = r.id
        LEFT JOIN refs ref ON ref.source_repo_id = r.id
        {repo_where}
        GROUP BY r.id, r.name, r.subsystem, r.path
        ORDER BY r.subsystem, r.name
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    _require_nonempty(repo_rows, "no indexed repositories match the system context request")

    edge_filter = []
    edge_params: list[object] = []
    if subsystem is not None:
        edge_filter.append("(sr.subsystem = ? OR tr.subsystem = ?)")
        edge_params.extend([subsystem, subsystem])
    if repo is not None:
        edge_filter.append("(sr.name = ? OR tr.name = ?)")
        edge_params.extend([repo, repo])
    edge_conditions = ["sr.id != tr.id"]
    edge_conditions.extend(edge_filter)
    edge_where = " WHERE " + " AND ".join(edge_conditions)
    edge_rows = conn.execute(
        f"""
        SELECT
          sr.name AS source_repo,
          sr.subsystem AS source_subsystem,
          tr.name AS target_repo,
          tr.subsystem AS target_subsystem,
          COUNT(ref.id) AS reference_count,
          ROUND(AVG(ref.confidence), 3) AS average_confidence,
          SUM(CASE WHEN ref.resolution_status = 'resolved' THEN 1 ELSE 0 END) AS resolved_count,
          SUM(CASE WHEN ref.resolution_status = 'unique_name' THEN 1 ELSE 0 END) AS unique_name_count,
          SUM(CASE WHEN ref.resolution_status = 'ambiguous' THEN 1 ELSE 0 END) AS ambiguous_count,
          SUM(CASE WHEN ref.resolution_status = 'unresolved' THEN 1 ELSE 0 END) AS unresolved_count
        FROM refs ref
        JOIN repos sr ON sr.id = ref.source_repo_id
        JOIN symbols ts ON ts.id = ref.target_symbol_id
        JOIN repos tr ON tr.id = ts.repo_id
        {edge_where}
        GROUP BY sr.id, sr.name, sr.subsystem, tr.id, tr.name, tr.subsystem
        ORDER BY sr.name, tr.name
        LIMIT ?
        """,
        (*edge_params, limit),
    ).fetchall()

    warnings = []
    if not edge_rows:
        warnings.append("no resolved cross-repo edges in the requested scope")
    if any(row["ambiguous_count"] or row["unresolved_count"] for row in edge_rows):
        warnings.append("cross-repo edges include ambiguous or unresolved resolutions")

    return {
        "layer": "system",
        "filters": {"subsystem": subsystem, "repo": repo},
        "repos": [
            {
                "name": row["name"],
                "subsystem": row["subsystem"],
                "path": row["path"],
                "file_count": row["file_count"],
                "symbol_count": row["symbol_count"],
                "exported_symbol_count": row["exported_symbol_count"],
                "outgoing_reference_count": row["outgoing_reference_count"],
            }
            for row in repo_rows
        ],
        "cross_repo_edges": [
            {
                "source_repo": row["source_repo"],
                "source_subsystem": row["source_subsystem"],
                "target_repo": row["target_repo"],
                "target_subsystem": row["target_subsystem"],
                "reference_count": row["reference_count"],
                "average_confidence": row["average_confidence"],
                "resolution_counts": {
                    "resolved": row["resolved_count"],
                    "unique_name": row["unique_name_count"],
                    "ambiguous": row["ambiguous_count"],
                    "unresolved": row["unresolved_count"],
                },
            }
            for row in edge_rows
        ],
        "warnings": warnings,
    }


def _file_row(conn: sqlite3.Connection, file_path: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT f.id, f.repo_id, f.path, f.language, f.module_name, f.status,
               r.name AS repo, r.subsystem
        FROM files f
        JOIN repos r ON r.id = f.repo_id
        WHERE f.path = ?
        """,
        (file_path,),
    ).fetchone()
    if row is None:
        raise FileNotFoundError(f"indexed file not found: {file_path}")
    return row


def _file_symbols(conn: sqlite3.Connection, file_id: int, limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, name, qualified_name, kind, parent_qualified,
               start_line, end_line, exported
        FROM symbols
        WHERE file_id = ?
        ORDER BY start_line
        LIMIT ?
        """,
        (file_id, limit),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "qualified_name": row["qualified_name"],
            "kind": row["kind"],
            "parent_qualified": row["parent_qualified"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "exported": bool(row["exported"]),
        }
        for row in rows
    ]


def _outbound_refs(conn: sqlite3.Connection, file_id: int, limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
          ref.target_name, ref.target_symbol, ref.resolution_status,
          ref.resolution_method, ref.confidence, ref.line, ref.kind,
          ts.file_id AS target_symbol_file_id, tf.path AS target_symbol_file
        FROM refs ref
        LEFT JOIN symbols ts ON ts.id = ref.target_symbol_id
        LEFT JOIN files tf ON tf.id = ts.file_id
        WHERE ref.source_file_id = ?
        ORDER BY ref.line
        LIMIT ?
        """,
        (file_id, limit),
    ).fetchall()
    return [
        {
            "target": row["target_name"],
            "resolved_symbol": row["target_symbol"],
            "resolution_status": row["resolution_status"],
            "resolution_method": row["resolution_method"],
            "confidence": row["confidence"],
            "line": row["line"],
            "kind": row["kind"],
            "target_file": row["target_symbol_file"],
        }
        for row in rows
    ]


def _inbound_refs_for_file(
    conn: sqlite3.Connection,
    file_id: int,
    symbol_ids: list[int],
    limit: int,
) -> list[dict]:
    if not symbol_ids:
        return []
    placeholders = ",".join("?" for _ in symbol_ids)
    rows = conn.execute(
        f"""
        SELECT
          ref.target_symbol_id, ts.name AS target_symbol,
          ts.qualified_name AS target_qualified_name,
          ref.resolution_status, ref.resolution_method, ref.confidence,
          ref.line, ref.kind,
          sr.name AS source_repo, sr.subsystem AS source_subsystem,
          sf.path AS source_file
        FROM refs ref
        JOIN symbols ts ON ts.id = ref.target_symbol_id
        JOIN files sf ON sf.id = ref.source_file_id
        JOIN repos sr ON sr.id = ref.source_repo_id
        WHERE ts.file_id = ? AND ref.target_symbol_id IN ({placeholders})
        ORDER BY sf.path, ref.line
        LIMIT ?
        """,
        (file_id, *symbol_ids, limit),
    ).fetchall()
    return [
        {
            "target_symbol": row["target_symbol"],
            "target_qualified_name": row["target_qualified_name"],
            "resolution_status": row["resolution_status"],
            "resolution_method": row["resolution_method"],
            "confidence": row["confidence"],
            "source_repo": row["source_repo"],
            "source_subsystem": row["source_subsystem"],
            "source_file": row["source_file"],
            "line": row["line"],
            "kind": row["kind"],
        }
        for row in rows
    ]


def module_context(
    conn: sqlite3.Connection,
    file_path: str,
    *,
    limit: int | None = None,
) -> dict:
    """Build a module-engineer packet for one indexed file."""
    limit = _limit(limit)
    file_row = _file_row(conn, file_path)
    symbols = _file_symbols(conn, file_row["id"], limit)
    outbound = _outbound_refs(conn, file_row["id"], limit)
    inbound = _inbound_refs_for_file(
        conn,
        file_row["id"],
        [item["id"] for item in symbols],
        limit,
    )
    related_files: set[str] = set()
    for item in inbound:
        related_files.add(item["source_file"])
    for item in outbound:
        if item["target_file"]:
            related_files.add(item["target_file"])
    related_files.discard(file_row["path"])
    warnings = []
    if not symbols:
        warnings.append("file has no indexed symbols")
    if not inbound:
        warnings.append("no inbound symbol references in current index")
    if any(item["resolution_status"] in {"ambiguous", "unresolved"} for item in outbound):
        warnings.append("outbound imports include ambiguous or unresolved edges")
    return {
        "layer": "module",
        "file": file_row["path"],
        "repo": file_row["repo"],
        "subsystem": file_row["subsystem"],
        "language": file_row["language"],
        "module": file_row["module_name"],
        "status": file_row["status"],
        "symbols": symbols,
        "outbound_imports": outbound,
        "inbound_references": inbound,
        "related_files": sorted(related_files),
        "warnings": warnings,
    }


def developer_context(
    conn: sqlite3.Connection,
    file_path: str,
    *,
    symbol: str | None = None,
    limit: int | None = None,
) -> dict:
    """Build a narrow developer packet; it intentionally contains no source text."""
    module_packet = module_context(conn, file_path, limit=limit)
    file_id = _file_row(conn, file_path)["id"]
    symbols = module_packet["symbols"]
    if symbol is not None:
        selected = [
            item
            for item in symbols
            if item["name"] == symbol or item["qualified_name"] == symbol
        ]
        if not selected:
            raise FileNotFoundError(
                f"symbol not found in {file_path}: {symbol}"
            )
    else:
        selected = [item for item in symbols if item["exported"]]
        if not selected:
            selected = symbols
    selected_ids = {item["id"] for item in selected}
    inbound = _inbound_refs_for_file(
        conn,
        file_id,
        list(selected_ids),
        _limit(limit),
    )
    if selected_ids:
        placeholders = ",".join("?" for _ in selected_ids)
        rows = conn.execute(
            f"""
            SELECT DISTINCT sf.path
            FROM refs ref
            JOIN symbols ts ON ts.id = ref.target_symbol_id
            JOIN files sf ON sf.id = ref.source_file_id
            WHERE ts.file_id = ? AND ref.target_symbol_id IN ({placeholders})
            """,
            (file_id, *selected_ids),
        ).fetchall()
        related = {row["path"] for row in rows}
    else:
        related = set()
    related.discard(module_packet["file"])
    related.update(
        item["target_file"]
        for item in module_packet["outbound_imports"]
        if item["target_file"]
    )
    parts = module_packet["file"].rsplit("/", 1)
    scope_candidates = [parts[0]] if len(parts) == 2 and parts[0] else []
    warnings = []
    if not selected:
        warnings.append("no target symbol selected")
    if not related and not inbound:
        warnings.append("no inbound or outbound related files")
    return {
        "layer": "developer",
        "file": module_packet["file"],
        "repo": module_packet["repo"],
        "subsystem": module_packet["subsystem"],
        "language": module_packet["language"],
        "module": module_packet["module"],
        "target_symbols": [
            {key: value for key, value in item.items() if key != "id"}
            for item in selected
        ],
        "outbound_imports": module_packet["outbound_imports"],
        "inbound_references": inbound,
        "related_files": sorted(related),
        "scope_candidates": scope_candidates,
        "warnings": warnings,
        "contains_source_text": False,
    }
