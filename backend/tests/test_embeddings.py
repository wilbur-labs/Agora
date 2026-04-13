"""Tests for embedding store and vector search."""
import tempfile
import shutil

import numpy as np
import pytest

from agora.embeddings.store import VectorStore


class TestVectorStore:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        import agora.config.settings as cfg
        self._old = cfg._config
        cfg._config = {"memory": {"data_dir": self.tmpdir}}
        self.store = VectorStore(db_path=self.tmpdir)

    def teardown_method(self):
        import agora.config.settings as cfg
        cfg._config = self._old
        self.store.close()
        shutil.rmtree(self.tmpdir)

    def test_add_and_count(self):
        v = np.random.randn(8).astype(np.float32)
        self.store.add("test", "hello", v)
        assert self.store.count("test") == 1

    def test_search_exact_match(self):
        v = np.random.randn(8).astype(np.float32)
        self.store.add("test", "target", v)
        self.store.add("test", "other", np.random.randn(8).astype(np.float32))
        results = self.store.search("test", v, top_k=1)
        assert results[0][0] == "target"
        assert results[0][1] > 0.99

    def test_search_ranking(self):
        base = np.array([1, 0, 0, 0], dtype=np.float32)
        similar = np.array([0.9, 0.1, 0, 0], dtype=np.float32)
        different = np.array([0, 0, 0, 1], dtype=np.float32)
        self.store.add("test", "similar", similar)
        self.store.add("test", "different", different)
        results = self.store.search("test", base, top_k=2)
        assert results[0][0] == "similar"
        assert results[0][1] > results[1][1]

    def test_search_empty_collection(self):
        v = np.random.randn(8).astype(np.float32)
        results = self.store.search("empty", v)
        assert results == []

    def test_clear(self):
        v = np.random.randn(8).astype(np.float32)
        self.store.add("test", "hello", v)
        self.store.clear("test")
        assert self.store.count("test") == 0

    def test_collections_isolated(self):
        v = np.random.randn(8).astype(np.float32)
        self.store.add("a", "in_a", v)
        self.store.add("b", "in_b", v)
        assert self.store.count("a") == 1
        assert self.store.count("b") == 1
        self.store.clear("a")
        assert self.store.count("a") == 0
        assert self.store.count("b") == 1

    def test_metadata_roundtrip(self):
        v = np.random.randn(8).astype(np.float32)
        self.store.add("test", "hello", v, {"type": "skill", "score": 5})
        results = self.store.search("test", v, top_k=1)
        assert results[0][2] == {"type": "skill", "score": 5}

    def test_top_k_limit(self):
        for i in range(10):
            self.store.add("test", f"item_{i}", np.random.randn(8).astype(np.float32))
        results = self.store.search("test", np.random.randn(8).astype(np.float32), top_k=3)
        assert len(results) == 3
