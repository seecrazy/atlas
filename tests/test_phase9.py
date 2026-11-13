import json
import math
import pathlib

from atlas.config import load_config, write_default_config
from atlas.db import connect
from atlas.embeddings import embed_text, rebuild_embeddings, vector_search
from atlas.indexer import index_project
from atlas.memory import generate_semantic_memories
from atlas.store import health, hybrid_search


def test_hash_embedding_is_normalized():
    vector = embed_text("retry timeout")
    assert len(vector) == 128
    assert math.isclose(sum(item * item for item in vector), 1.0, rel_tol=1e-6)


def test_structured_memory_fields_and_embeddings(project_root):
    config = load_config(project_root)
    conn = connect(config.db_path)
    try:
        result = generate_semantic_memories(conn, config, scope="retry", use_llm=False)
        assert result["count"] > 0
        rows = conn.execute(
            """
            SELECT m.memory_key, f.field_name, f.value_json
            FROM memories m
            JOIN memory_fields f ON f.memory_id = m.id
            WHERE m.claim LIKE '%retry%'
            """
        ).fetchall()
        fields = {
            row["memory_key"]: {
                item["field_name"]: json.loads(item["value_json"])
                for item in rows
                if item["memory_key"] == row["memory_key"]
            }
            for row in rows
        }
        assert fields
        assert any("entity_type" in field for field in fields.values())
        assert any("exported_symbols" in field for field in fields.values())
        assert health(conn)["embedding_count"] > 0
    finally:
        conn.close()


def test_vector_and_hybrid_search(project_root):
    config = load_config(project_root)
    index_project(config, full=True)
    conn = connect(config.db_path)
    generate_semantic_memories(conn, config, use_llm=False)
    rebuild_embeddings(conn)
    try:
        vector = vector_search(conn, "RetryClient", limit=10)
        assert vector
        assert any(item["doc_type"] == "symbol" for item in vector)
        hybrid = hybrid_search(conn, "RetryClient", limit=20)
        assert hybrid
        assert any(item.get("vector_similarity", 0) > 0 for item in hybrid)
    finally:
        conn.close()
