"""Skill store — load, save, match, and track reusable skills."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from agora.config.settings import get_config


@dataclass
class Skill:
    name: str
    trigger: str
    type: str = "execution"  # "execution" | "discussion"
    steps: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    success_count: int = 0
    fail_count: int = 0
    source_file: Optional[Path] = None

    def to_yaml(self) -> str:
        data = {
            "name": self.name,
            "type": self.type,
            "trigger": self.trigger,
            "steps": self.steps,
        }
        if self.lessons:
            data["lessons"] = self.lessons
        if self.success_count or self.fail_count:
            data["success_count"] = self.success_count
            data["fail_count"] = self.fail_count
        return yaml.dump(data, allow_unicode=True, default_flow_style=False)

    @classmethod
    def from_yaml(cls, text: str, source: Path | None = None) -> Skill:
        data = yaml.safe_load(text) or {}
        return cls(
            name=data.get("name", "unnamed"),
            trigger=data.get("trigger", ""),
            type=data.get("type", "execution"),
            steps=data.get("steps", []),
            lessons=data.get("lessons", []),
            success_count=data.get("success_count", 0),
            fail_count=data.get("fail_count", 0),
            source_file=source,
        )


class SkillStore:
    def __init__(self):
        cfg = get_config().get("skills", {})
        self.enabled = cfg.get("enabled", False)
        self.paths = [Path(p) for p in cfg.get("paths", [])]
        self._skills: list[Skill] | None = None

    def _load_all(self) -> list[Skill]:
        skills = []
        for d in self.paths:
            if not d.exists():
                continue
            for f in sorted(d.glob("*.yaml")):
                try:
                    skills.append(Skill.from_yaml(f.read_text(), source=f))
                except Exception:
                    continue
        return skills

    @property
    def skills(self) -> list[Skill]:
        if self._skills is None:
            self._skills = self._load_all()
        return self._skills

    def match(self, query: str) -> list[Skill]:
        if not self.enabled or not self.skills:
            return []
        query_lower = query.lower()
        matched = []
        for skill in self.skills:
            trigger = skill.trigger.lower()
            if any(w in query_lower for w in trigger.split() if len(w) > 1):
                matched.append(skill)
            elif any(w in trigger for w in query_lower.split() if len(w) > 1):
                matched.append(skill)
        return matched

    async def match_embedding(self, query: str, embedding_provider, vector_store) -> list[Skill]:
        """Match skills using embedding similarity. Falls back to keyword match."""
        if not self.enabled or not self.skills:
            return []
        try:
            q_emb = await embedding_provider.embed([query])
            results = vector_store.search("skills", q_emb[0], top_k=3)
            if not results:
                return self.match(query)
            matched_names = {text.split("|")[0].strip() for text, score, _ in results if score > 0.5}
            return [s for s in self.skills if s.name in matched_names]
        except Exception:
            return self.match(query)

    async def index_skills(self, embedding_provider, vector_store):
        """Index all skills into vector store."""
        if not self.skills:
            return
        texts = [f"{s.name} | {s.trigger}" for s in self.skills]
        embeddings = await embedding_provider.embed(texts)
        vector_store.clear("skills")
        for text, emb in zip(texts, embeddings):
            vector_store.add("skills", text, emb)
        """Use LLM to semantically match skills. Falls back to keyword match on failure."""
        if not self.enabled or not self.skills:
            return []
        if len(self.skills) > 20:
            # Pre-filter with keywords first to keep LLM input small
            candidates = self.match(query)
            if not candidates:
                candidates = self.skills[:20]
        else:
            candidates = self.skills

        skill_list = "\n".join(f"{i}. [{s.name}] {s.trigger}" for i, s in enumerate(candidates))
        try:
            response = await provider.generate([
                {"role": "system", "content": (
                    "Given a user query and a list of skills, return the indices of relevant skills. "
                    "Output ONLY comma-separated numbers (e.g. '0,2,5') or 'NONE'."
                )},
                {"role": "user", "content": f"Query: {query}\n\nSkills:\n{skill_list}"},
            ])
            if "NONE" in response.upper():
                return []
            indices = [int(x.strip()) for x in response.strip().split(",") if x.strip().isdigit()]
            return [candidates[i] for i in indices if i < len(candidates)]
        except Exception:
            return self.match(query)

    def save(self, skill: Skill) -> Path:
        learned_dir = None
        for p in self.paths:
            if "learned" in str(p):
                learned_dir = p
                break
        if not learned_dir:
            learned_dir = self.paths[-1] if self.paths else Path("./skills/learned")
        learned_dir = learned_dir.resolve()
        learned_dir.mkdir(parents=True, exist_ok=True)

        filename = skill.name.replace(" ", "_").lower() + ".yaml"
        path = learned_dir / filename

        # If skill already exists, merge counts
        if path.exists():
            try:
                existing = Skill.from_yaml(path.read_text(), source=path)
                skill.success_count += existing.success_count
                skill.fail_count += existing.fail_count
            except Exception:
                pass

        path.write_text(skill.to_yaml())
        self._skills = None
        return path

    def record_outcome(self, skill_name: str, success: bool):
        """Increment success or fail count for a skill."""
        for skill in self.skills:
            if skill.name == skill_name and skill.source_file:
                if success:
                    skill.success_count += 1
                else:
                    skill.fail_count += 1
                skill.source_file.write_text(skill.to_yaml())
                return

    def get_injection_text(self, query: str) -> str:
        matched = self.match(query)
        if not matched:
            return ""
        parts = []
        for s in matched[:3]:
            lines = [f"Skill: {s.name} ({s.type})", f"Trigger: {s.trigger}"]
            if s.success_count or s.fail_count:
                lines.append(f"Track record: {s.success_count} successes, {s.fail_count} failures")
            if s.steps:
                lines.append("Steps:\n" + "\n".join(f"  - {st}" for st in s.steps))
            if s.lessons:
                lines.append("Lessons:\n" + "\n".join(f"  - {ls}" for ls in s.lessons))
            parts.append("\n".join(lines))
        return "RELEVANT SKILLS (from past experience):\n\n" + "\n---\n".join(parts)
