"""Reports downstream exposure for changed exported symbols."""

from __future__ import annotations

import sqlite3

from .store import _confidence, _limit, references


def export_propagation(
    conn: sqlite3.Connection,
    *,
    limit: int | None = 50,
    min_confidence: float | None = 0.65,
) -> dict:
    threshold = _confidence(min_confidence)
    limit = _limit(limit)
    rows = conn.execute(
        """
        SELECT c.id, c.file_path, c.symbol, c.qualified_name,
               c.change_type, c.indexed_at, r.name AS repo, r.subsystem
        FROM exported_symbol_changes c
        JOIN repos r ON r.id = c.repo_id
        ORDER BY c.indexed_at DESC, c.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    changes = []
    affected_repos: set[str] = set()
    downstream_reference_count = 0
    for row in rows:
        refs = references(
            conn,
            row["qualified_name"],
            limit=limit,
            min_confidence=threshold,
        )
        downstream_reference_count += len(refs)
        affected_repos.update(ref["source_repo"] for ref in refs)
        changes.append(
            {
                "file": row["file_path"],
                "repo": row["repo"],
                "subsystem": row["subsystem"],
                "symbol": row["symbol"],
                "qualified_name": row["qualified_name"],
                "change_type": row["change_type"],
                "indexed_at": row["indexed_at"],
                "downstream_reference_count": len(refs),
                "references": refs,
            }
        )
    return {
        "min_confidence": threshold,
        "symbol_change_count": len(changes),
        "downstream_reference_count": downstream_reference_count,
        "affected_repos": sorted(affected_repos),
        "changes": changes,
        "warnings": [] if changes else ["no exported symbol changes are recorded"],
    }
