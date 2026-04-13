"""Tests for memory store and skill store."""
import os
import tempfile
import shutil

import pytest

from agora.memory.store import MemoryStore
from agora.skills.store import Skill, SkillStore


# ── MemoryStore ──

class TestMemoryStore:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        # Patch config to use tmpdir
        import agora.config.settings as cfg
        self._old = cfg._config
        cfg._config = {"memory": {"data_dir": self.tmpdir, "memory_char_limit": 500, "user_char_limit": 300}}
        self.store = MemoryStore(data_dir=self.tmpdir)

    def teardown_method(self):
        import agora.config.settings as cfg
        cfg._config = self._old
        shutil.rmtree(self.tmpdir)

    def test_add_and_read(self):
        ok, msg = self.store.add("memory", "fact one")
        assert ok is True
        text = self.store.get_injection_text()
        assert "fact one" in text

    def test_add_duplicate(self):
        self.store.add("memory", "fact one")
        ok, msg = self.store.add("memory", "fact one")
        assert ok is True
        assert "exists" in msg.lower()

    def test_add_exceeds_limit(self):
        # Fill up memory
        self.store.add("memory", "x" * 400)
        ok, msg = self.store.add("memory", "y" * 200)
        assert ok is False
        assert "limit" in msg.lower()

    def test_remove(self):
        self.store.add("memory", "to remove")
        ok, _ = self.store.remove("memory", "to remove")
        assert ok is True
        assert "to remove" not in self.store.get_injection_text()

    def test_replace(self):
        self.store.add("memory", "old fact")
        ok, _ = self.store.replace("memory", "old fact", "new fact")
        assert ok is True
        text = self.store.get_injection_text()
        assert "new fact" in text
        assert "old fact" not in text

    def test_user_profile(self):
        ok, _ = self.store.add("user", "prefers Python")
        assert ok is True
        text = self.store.get_injection_text()
        assert "prefers Python" in text

    def test_injection_text_shows_usage(self):
        self.store.add("memory", "some fact")
        text = self.store.get_injection_text()
        assert "%" in text  # shows usage percentage
        assert "chars" in text


# ── Skill ──

class TestSkill:
    def test_to_yaml_roundtrip(self):
        s = Skill(
            name="test_skill", trigger="when testing", type="execution",
            steps=["step 1", "step 2"], lessons=["lesson 1"],
            success_count=5, fail_count=1,
        )
        yaml_text = s.to_yaml()
        s2 = Skill.from_yaml(yaml_text)
        assert s2.name == "test_skill"
        assert s2.trigger == "when testing"
        assert s2.type == "execution"
        assert s2.steps == ["step 1", "step 2"]
        assert s2.lessons == ["lesson 1"]
        assert s2.success_count == 5
        assert s2.fail_count == 1

    def test_discussion_type(self):
        s = Skill(name="d", trigger="t", type="discussion")
        s2 = Skill.from_yaml(s.to_yaml())
        assert s2.type == "discussion"

    def test_defaults(self):
        s = Skill.from_yaml("name: x\ntrigger: y")
        assert s.type == "execution"
        assert s.success_count == 0
        assert s.steps == []


# ── SkillStore ──

class TestSkillStore:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.learned_dir = os.path.join(self.tmpdir, "learned")
        os.makedirs(self.learned_dir)
        import agora.config.settings as cfg
        self._old = cfg._config
        cfg._config = {"skills": {"enabled": True, "paths": [self.learned_dir]}}
        self.store = SkillStore()

    def teardown_method(self):
        import agora.config.settings as cfg
        cfg._config = self._old
        shutil.rmtree(self.tmpdir)

    def test_save_and_load(self):
        skill = Skill(name="my_skill", trigger="when doing X", steps=["do A"])
        path = self.store.save(skill)
        assert path.exists()
        # Reload
        self.store._skills = None
        assert len(self.store.skills) == 1
        assert self.store.skills[0].name == "my_skill"

    def test_keyword_match(self):
        self.store._skills = [
            Skill(name="cache_setup", trigger="adding cache layer"),
            Skill(name="auth_setup", trigger="authentication login"),
        ]
        matched = self.store.match("I need to add a cache")
        assert any(s.name == "cache_setup" for s in matched)

    def test_keyword_no_match(self):
        self.store._skills = [
            Skill(name="cache_setup", trigger="adding cache layer"),
        ]
        matched = self.store.match("deploy to kubernetes")
        assert len(matched) == 0

    def test_record_outcome(self):
        skill = Skill(name="test_skill", trigger="test", success_count=0)
        path = self.store.save(skill)
        self.store._skills = None  # force reload
        self.store.record_outcome("test_skill", success=True)
        self.store._skills = None
        reloaded = self.store.skills[0]
        assert reloaded.success_count == 1

    def test_save_merges_counts(self):
        s1 = Skill(name="s", trigger="t", success_count=3)
        self.store.save(s1)
        s2 = Skill(name="s", trigger="t", success_count=2)
        self.store.save(s2)
        self.store._skills = None
        assert self.store.skills[0].success_count == 5

    def test_injection_text_format(self):
        self.store._skills = [
            Skill(name="my_skill", trigger="cache setup", type="execution",
                  steps=["step1"], lessons=["lesson1"], success_count=3, fail_count=1),
        ]
        text = self.store.get_injection_text("cache")
        assert "my_skill" in text
        assert "execution" in text
        assert "3 successes" in text
        assert "step1" in text
        assert "lesson1" in text

    def test_disabled_store_returns_nothing(self):
        import agora.config.settings as cfg
        cfg._config = {"skills": {"enabled": False, "paths": [self.learned_dir]}}
        store = SkillStore()
        store._skills = [Skill(name="x", trigger="everything")]
        assert store.match("everything") == []
        assert store.get_injection_text("everything") == ""
