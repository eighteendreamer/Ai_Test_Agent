from __future__ import annotations

from src.registry.skills import SkillRegistry


class SkillRuntimeService:
    def __init__(self, skill_registry: SkillRegistry) -> None:
        self._skill_registry = skill_registry

    def build_prompt_blocks(self, skill_keys: list[str]) -> list[str]:
        blocks: list[str] = []
        for skill in self._skill_registry.get_many(skill_keys):
            blocks.append(
                f"- {skill.name}: {skill.description} "
                f"(focus tags: {', '.join(skill.tags) or 'general'})"
            )
        return blocks
