"""Dependency-free hashed embedding index and vector similarity."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3

from .db import audit, utcnow


MODEL = "atlas-hash-v1"
DIMENSION = 128


def embed_text(text: str, dimension: int = DIMENSION) -> list[float]:
    vector = [0.0] * dimension
    for token in re.findall(r"[\w]{2,}", text.lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % dimension
        sign = 1.0 if digest[8] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(item * item for item in vector))
    if norm > 0:
        return [round(item / norm, 8) for item in vector]
    return vector


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def upsert_embedding(
    conn: sqlite3.Connection,
    object_type: str,
    object_id: int,
    text: str,
    *,
    model: str = MODEL,
    dimension: int = DIMENSION,
) -> None:
    vector = embed_text(text, dimension)
    conn.execute(
        """
        INSERT INTO embeddings(object_type, object_id, model, dimension, vector_json, created_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(object_type, object_id, model) DO UPDATE SET
          dimension=excluded.dimension,
          vector_json=excluded.vector_json,
          created_at=excluded.created_at
        """,
        (
            object_type,
            int(object_id),
            model,
            int(dimension),
            json.dumps(vector),
            utcnow(),
        ),
    )


def rebuild_embeddings(conn: sqlite3.Connection) -> dict:
    conn.execute("DELETE FROM embeddings WHERE model = ?", (MODEL,))
    count = 0
    symbol_rows = conn.execute(
        """
        SELECT s.id, s.name, s.qualified_name, s.kind, f.path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        """
    ).fetchall()
    for row in symbol_rows:
        upsert_embedding(conn, "symbol", row["id"], f"{row['name']} {row['qualified_name']} {row['kind']} {row['path']}")
        count += 1
    memory_rows = conn.execute("SELECT id, claim FROM memories").fetchall()
    for row in memory_rows:
        upsert_embedding(conn, "memory", row["id"], row["claim"])
        count += 1
    contract_rows = conn.execute("SELECT id, name, notes, consumers_json FROM contracts").fetchall()
    for row in contract_rows:
        upsert_embedding(conn, "contract", row["id"], f"{row['name']} {row['notes']} {row['consumers_json']}")
        count += 1
    doc_rows = conn.execute("SELECT id, title, summary, path FROM architecture_docs").fetchall()
    for row in doc_rows:
        upsert_embedding(conn, "architecture_doc", row["id"], f"{row['title']} {row['summary']} {row['path']}")
        count += 1
    audit(conn, "embeddings.rebuilt", {"count": count, "model": MODEL, "dimension": DIMENSION})
    conn.commit()
    return {"indexed": count, "model": MODEL, "dimension": DIMENSION}


def vector_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
    min_similarity: float = 0.05,
) -> list[dict]:
    query_vector = embed_text(query)
    rows = conn.execute(
        "SELECT object_type, object_id, model, dimension, vector_json FROM embeddings WHERE model = ?",
        (MODEL,),
    ).fetchall()
    scored = []
    for row in rows:
        try:
            vector = json.loads(row["vector_json"])
        except json.JSONDecodeError:
            continue
        similarity = cosine_similarity(query_vector, vector)
        if similarity < min_similarity:
            continue
        scored.append(
            {
                "doc_type": row["object_type"],
                "doc_id": row["object_id"],
                "similarity": round(similarity, 4),
            }
        )
    return sorted(scored, key=lambda item: item["similarity"], reverse=True)[: max(1, min(int(limit), 200))]
