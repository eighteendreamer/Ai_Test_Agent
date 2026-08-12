from __future__ import annotations

from src.registry.skills import SkillRegistry


class SkillRuntimeService:
    def __init__(self, skill_registry: SkillRegistry) -> None:
        self._skill_registry = skill_registry
        self._skills_root = skill_registry.skills_root

    def build_prompt_blocks(
        self,
        skill_keys: list[str],
        *,
        include_content: bool = False,
    ) -> list[str]:
        """Expose metadata during routing and full content only after Skill load."""
        blocks: list[str] = []
        for skill in self._skill_registry.get_many(skill_keys):
            skill_file = self._skills_root / skill.key / "SKILL.md"
            if include_content and skill_file.exists():
                content = skill_file.read_text(encoding="utf-8")
                references = self.list_references(skill.key)
                reference_catalog = ""
                if references:
                    reference_catalog = (
                        "\nAvailable references (load only when needed with the registered `skill` tool):\n"
                        + "\n".join(f"- {path}" for path in references)
                    )
                blocks.append(
                    f"## Skill: {skill.name}\n"
                    f"Source: {skill_file}\n"
                    f"{content.strip()}"
                    f"{reference_catalog}"
                )
                continue
            blocks.append(
                f"- {skill.key}: {skill.name} - {skill.description} "
                f"(focus tags: {', '.join(skill.tags) or 'general'}). "
                "Use the registered `skill` loader with this key to load the full instructions."
            )
        return blocks

    def list_references(self, skill_key: str) -> list[str]:
        if not self._skill_registry.get_many([skill_key]):
            return []
        references_root = self._skills_root / skill_key / "references"
        if not references_root.is_dir():
            return []
        return [
            path.relative_to(self._skills_root / skill_key).as_posix()
            for path in sorted(references_root.rglob("*"))
            if path.is_file()
        ]

    def read_reference(self, skill_key: str, reference_path: str) -> str:
        if not self._skill_registry.get_many([skill_key]):
            raise KeyError(f"Unknown skill: {skill_key}")
        skill_root = (self._skills_root / skill_key).resolve()
        references_root = (skill_root / "references").resolve()
        candidate = (skill_root / reference_path).resolve()
        if candidate == references_root or references_root not in candidate.parents:
            raise ValueError("Skill reference path must stay within the Skill references directory.")
        if not candidate.is_file():
            raise FileNotFoundError(f"Skill reference not found: {reference_path}")
        return candidate.read_text(encoding="utf-8")
