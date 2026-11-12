"""Architecture contracts, documents, and contract impact queries."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sqlite3

from .config import ProjectConfig
from .db import audit, utcnow
from .store import references


def _contract_key(name: str, provider_repo: str) -> str:
    raw = f"{provider_repo}:{name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def sync_contracts(conn: sqlite3.Connection, config: ProjectConfig) -> dict:
    synced = []
    for item in config.contracts:
        name = str(item.get("name", "")).strip()
        provider_repo = str(item.get("provider_repo", "")).strip()
        if not name or not provider_repo:
            continue
        repo_row = conn.execute("SELECT subsystem FROM repos WHERE name = ?", (provider_repo,)).fetchone()
        subsystem = repo_row["subsystem"] if repo_row else str(item.get("provider_subsystem", "unknown"))
        consumers = item.get("consumers", [])
        if not isinstance(consumers, list):
            consumers = []
        key = _contract_key(name, provider_repo)
        now = utcnow()
        conn.execute(
            """
            INSERT INTO contracts(
              contract_key, name, provider_repo, provider_subsystem, version,
              status, consumers_json, notes, created_at, updated_at
            ) VALUES(?,?,?,?,?, 'active', ?, ?, ?, ?)
            ON CONFLICT(contract_key) DO UPDATE SET
              version=excluded.version, status='active',
              consumers_json=excluded.consumers_json, notes=excluded.notes,
              updated_at=excluded.updated_at
            """,
            (
                key, name, provider_repo, subsystem, str(item.get("version", "v1")),
                json.dumps(consumers, ensure_ascii=False), item.get("notes"), now, now,
            ),
        )
        synced.append({"contract_key": key, "name": name, "provider_repo": provider_repo})
    audit(conn, "contracts.synced", {"count": len(synced)})
    conn.commit()
    return {"synced": synced, "count": len(synced)}


def list_contracts(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM contracts ORDER BY name LIMIT ?", (max(1, min(int(limit), 200)),)
    ).fetchall()
    return [
        {
            **dict(row),
            "consumers": json.loads(row["consumers_json"]),
        }
        for row in rows
    ]


def contract_impact(
    conn: sqlite3.Connection,
    contract_name: str,
    *,
    limit: int = 100,
    min_confidence: float | None = 0.65,
) -> dict:
    row = conn.execute("SELECT * FROM contracts WHERE name = ? ORDER BY updated_at DESC LIMIT 1", (contract_name,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"contract not found: {contract_name}")
    refs = references(conn, row["name"], limit=limit, min_confidence=min_confidence)
    affected = sorted({ref["source_repo"] for ref in refs})
    return {
        "contract": {**dict(row), "consumers": json.loads(row["consumers_json"])},
        "reference_count": len(refs),
        "affected_repos": affected,
        "references": refs,
    }


def _doc_kind(path: pathlib.Path, title: str) -> str:
    lowered = (str(path).lower() + " " + title.lower())
    if "adr" in lowered:
        return "adr"
    if "api" in lowered:
        return "api"
    if "schema" in lowered or "database" in lowered or "sql" in lowered:
        return "database"
    if "contract" in lowered:
        return "contract"
    return "architecture"


def ingest_architecture_docs(conn: sqlite3.Connection, config: ProjectConfig) -> dict:
    indexed = []
    now = utcnow()
    seen: set[str] = set()
    for configured in config.doc_paths:
        root = (config.project_root / configured).resolve()
        if not root.exists():
            continue
        files = root.rglob("*") if root.is_dir() else [root]
        for path in files:
            if not path.is_file() or path.suffix.lower() not in {".md", ".markdown", ".txt", ".json", ".yaml", ".yml"}:
                continue
            stable = path.relative_to(config.project_root).as_posix()
            if stable in seen:
                continue
            seen.add(stable)
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else path.stem
            paragraphs = [item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()]
            summary = " ".join(paragraphs[:3])[:1200]
            kind = _doc_kind(path, title)
            doc_key = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]
            conn.execute(
                """
                INSERT INTO architecture_docs(
                  doc_key, path, kind, title, summary, content_hash, indexed_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(doc_key) DO UPDATE SET
                  kind=excluded.kind, title=excluded.title, summary=excluded.summary,
                  content_hash=excluded.content_hash, indexed_at=excluded.indexed_at
                """,
                (
                    doc_key, stable, kind, title, summary,
                    hashlib.sha256(content.encode("utf-8")).hexdigest(), now,
                ),
            )
            indexed.append({"path": stable, "kind": kind, "title": title})
    audit(conn, "architecture_docs.indexed", {"count": len(indexed)})
    conn.commit()
    return {"indexed": indexed, "count": len(indexed)}


def list_architecture_docs(conn: sqlite3.Connection, kind: str | None = None, limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM architecture_docs"
    params: list[object] = []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)
    sql += " ORDER BY path LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    return [dict(row) for row in conn.execute(sql, params)]
