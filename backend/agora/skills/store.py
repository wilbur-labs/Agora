"""Skill store — load, save, and match reusable skills."""
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
    steps: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    source_file: Optional[Path] = None

    def to_yaml(self) -> str:
        data = {"name": self.name, "trigger": self.trigger, "steps": self.steps}
        if self.lessons:
            data["lessons"] = self.lessons
        return yaml.dump(data, allow_unicode=True, default_flow_style=False)

    @classmethod
    def from_yaml(cls, text: str, source: Path | None = None) -> Skill:
        data = yaml.safe_load(text) or {}
        return cls(
            name=data.get("name", "unnamed"),
            trigger=data.get("trigger", ""),
            steps=data.get("steps", []),
            lessons=data.get("lessons", []),
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
        """Match skills whose trigger has overlapping tokens with query."""
        if not self.enabled:
            return []
        query_lower = query.lower()
        matched = []
        for skill in self.skills:
            trigger = skill.trigger.lower()
            # Check substring match (works for CJK) and word overlap (works for English)
            if any(w in query_lower for w in trigger.split() if len(w) > 1):
                matched.append(skill)
            elif any(w in trigger for w in query_lower.split() if len(w) > 1):
                matched.append(skill)
        return matched

    def save(self, skill: Skill) -> Path:
        """Save a learned skill to the first writable 'learned' path."""
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
        path.write_text(skill.to_yaml())

        # Invalidate cache
        self._skills = None
        return path

    def get_injection_text(self, query: str) -> str:
        """Get matched skills formatted for prompt injection."""
        matched = self.match(query)
        if not matched:
            return ""
        parts = []
        for s in matched[:3]:  # max 3 skills injected
            lines = [f"Skill: {s.name}", f"Trigger: {s.trigger}"]
            if s.steps:
                lines.append("Steps:\n" + "\n".join(f"  - {st}" for st in s.steps))
            if s.lessons:
                lines.append("Lessons:\n" + "\n".join(f"  - {ls}" for ls in s.lessons))
            parts.append("\n".join(lines))
        return "RELEVANT SKILLS (from past experience):\n\n" + "\n---\n".join(parts)
