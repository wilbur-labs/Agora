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


def test_a_relative_data_dir_anchors_the_vector_store_to_the_config_file(tmp_path, monkeypatch):
    """`memory.data_dir` has to mean the same thing for both databases it names.

    It was anchored for the control plane and left cwd-relative here, so one key
    resolved two ways. Nothing covered it: every other test in this file passes an
    absolute db_path, so the anchoring code was never executed by the suite."""
    import agora.config.settings as settings
    from agora.embeddings.store import VectorStore

    install = tmp_path / "install"
    install.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (install / "config.yaml").write_text("memory:\n  data_dir: ./vectors\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("AGORA_CONFIG_PATH", str(install / "config.yaml"))
    settings.reset_config()

    store = VectorStore()
    try:
        assert (install / "vectors" / "vectors.db").exists()
        assert not (elsewhere / "vectors").exists()
    finally:
        store.conn.close()
        settings.reset_config()
