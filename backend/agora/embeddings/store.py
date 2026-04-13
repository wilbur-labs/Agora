"""Vector store — SQLite-backed embedding storage with cosine similarity search."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from agora.config.settings import get_config


class VectorStore:
    def __init__(self, db_path: str | Path | None = None):
        cfg = get_config().get("memory", {})
        p = Path(db_path or cfg.get("data_dir", "./data")) / "vectors.db"
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  collection TEXT NOT NULL,"
            "  text TEXT NOT NULL,"
            "  metadata TEXT DEFAULT '{}',"
            "  embedding BLOB NOT NULL"
            ")"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_collection ON vectors(collection)")
        self.conn.commit()

    def add(self, collection: str, text: str, embedding: np.ndarray, metadata: dict | None = None):
        self.conn.execute(
            "INSERT INTO vectors (collection, text, metadata, embedding) VALUES (?, ?, ?, ?)",
            (collection, text, json.dumps(metadata or {}), embedding.astype(np.float32).tobytes()),
        )
        self.conn.commit()

    def search(self, collection: str, query_embedding: np.ndarray, top_k: int = 5) -> list[tuple[str, float, dict]]:
        """Returns list of (text, similarity_score, metadata)."""
        rows = self.conn.execute(
            "SELECT text, embedding, metadata FROM vectors WHERE collection = ?", (collection,)
        ).fetchall()
        if not rows:
            return []

        dim = query_embedding.shape[0]
        q = query_embedding.astype(np.float32)
        q_norm = q / (np.linalg.norm(q) + 1e-10)

        results = []
        for text, emb_bytes, meta_str in rows:
            vec = np.frombuffer(emb_bytes, dtype=np.float32)
            if vec.shape[0] != dim:
                continue
            v_norm = vec / (np.linalg.norm(vec) + 1e-10)
            score = float(np.dot(q_norm, v_norm))
            results.append((text, score, json.loads(meta_str)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def count(self, collection: str) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM vectors WHERE collection = ?", (collection,)).fetchone()
        return row[0]

    def clear(self, collection: str):
        self.conn.execute("DELETE FROM vectors WHERE collection = ?", (collection,))
        self.conn.commit()

    def close(self):
        self.conn.close()
