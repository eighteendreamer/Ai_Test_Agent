from __future__ import annotations

import logging
from pathlib import Path

from src.registry.skills import SkillRegistry


def _write_skill(root: Path, key: str, description: str) -> Path:
    path = root / key / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {key}\ndescription: {description}\n---\n# Body\n",
        encoding="utf-8",
    )
    return path


def test_bad_utf8_skill_does_not_block_registry_startup(tmp_path: Path, caplog) -> None:
    _write_skill(tmp_path, "healthy", "Healthy skill")
    broken = tmp_path / "broken" / "SKILL.md"
    broken.parent.mkdir()
    broken.write_bytes(b"\xff")

    with caplog.at_level(logging.WARNING):
        registry = SkillRegistry(skills_root=tmp_path)

    assert registry.get("healthy").description == "Healthy skill"
    assert registry.get("ci-pipeline-review").key == "ci-pipeline-review"
    assert "broken" not in {item.key for item in registry.list()}
    assert any(
        record.message == "skill_file_load_failed"
        and record.skill_key == "broken"
        and record.error_type == "UnicodeDecodeError"
        for record in caplog.records
    )


def test_reload_publishes_healthy_skills_when_one_file_becomes_unreadable(
    tmp_path: Path, caplog,
) -> None:
    original = _write_skill(tmp_path, "first", "Original description")
    registry = SkillRegistry(skills_root=tmp_path)
    _write_skill(tmp_path, "second", "New description")
    original.write_bytes(b"\xff")

    with caplog.at_level(logging.WARNING):
        registry.reload()

    assert "first" not in {item.key for item in registry.list()}
    assert registry.get("second").description == "New description"
    assert any(record.message == "skill_file_load_failed" for record in caplog.records)

    _write_skill(tmp_path, "first", "Repaired description")
    registry.reload()
    assert registry.get("first").description == "Repaired description"
