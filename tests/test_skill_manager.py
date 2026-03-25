"""Tests for SkillManager install/export/list/remove."""

import pytest
from pathlib import Path

import yaml

from opentf.core.skill_manager import SkillManager


VALID_SKILL = {
    "name": "test_skill",
    "description": "A test skill",
    "capabilities": ["testing", "demo"],
    "system_prompt": "You are a test agent.",
}


@pytest.fixture()
def manager(tmp_path: Path) -> SkillManager:
    return SkillManager(skills_dir=tmp_path / "skills")


@pytest.fixture()
def skill_file(tmp_path: Path) -> Path:
    path = tmp_path / "source_skill.yaml"
    path.write_text(yaml.dump(VALID_SKILL))
    return path


# --- Validation ---

def test_validate_valid(manager: SkillManager) -> None:
    ok, err = manager.validate(VALID_SKILL)
    assert ok
    assert err == ""


def test_validate_missing_name(manager: SkillManager) -> None:
    data = {k: v for k, v in VALID_SKILL.items() if k != "name"}
    ok, err = manager.validate(data)
    assert not ok
    assert "name" in err


def test_validate_missing_system_prompt(manager: SkillManager) -> None:
    data = {k: v for k, v in VALID_SKILL.items() if k != "system_prompt"}
    ok, err = manager.validate(data)
    assert not ok
    assert "system_prompt" in err


def test_validate_invalid_name(manager: SkillManager) -> None:
    data = {**VALID_SKILL, "name": "bad name!@#"}
    ok, err = manager.validate(data)
    assert not ok
    assert "alphanumeric" in err


def test_validate_capabilities_not_list(manager: SkillManager) -> None:
    data = {**VALID_SKILL, "capabilities": "not a list"}
    ok, err = manager.validate(data)
    assert not ok
    assert "list" in err


# --- Install from path ---

@pytest.mark.asyncio
async def test_install_from_path(manager: SkillManager, skill_file: Path) -> None:
    ok, msg = await manager.install_from_path(str(skill_file))
    assert ok
    assert "Installed" in msg
    # Verify file created in skills_dir
    assert (manager.skills_dir / "test_skill.yaml").exists()


@pytest.mark.asyncio
async def test_install_from_path_not_found(manager: SkillManager) -> None:
    ok, msg = await manager.install_from_path("/nonexistent/skill.yaml")
    assert not ok
    assert "not found" in msg.lower()


@pytest.mark.asyncio
async def test_install_duplicate(manager: SkillManager, skill_file: Path) -> None:
    await manager.install_from_path(str(skill_file))
    ok, msg = await manager.install_from_path(str(skill_file))
    assert not ok
    assert "already exists" in msg


@pytest.mark.asyncio
async def test_install_invalid_extension(manager: SkillManager, tmp_path: Path) -> None:
    bad = tmp_path / "skill.txt"
    bad.write_text(yaml.dump(VALID_SKILL))
    ok, msg = await manager.install_from_path(str(bad))
    assert not ok
    assert ".yaml" in msg


# --- Export ---

@pytest.mark.asyncio
async def test_export_roundtrip(manager: SkillManager, skill_file: Path) -> None:
    await manager.install_from_path(str(skill_file))
    exported = manager.export_skill("test_skill")
    assert exported is not None
    data = yaml.safe_load(exported)
    assert data["name"] == "test_skill"
    assert data["description"] == "A test skill"


def test_export_not_found(manager: SkillManager) -> None:
    assert manager.export_skill("nonexistent") is None


# --- List ---

def test_list_empty(manager: SkillManager) -> None:
    skills = manager.list_skills()
    assert skills == []


@pytest.mark.asyncio
async def test_list_with_metadata(manager: SkillManager, skill_file: Path) -> None:
    await manager.install_from_path(str(skill_file))
    skills = manager.list_skills()
    assert len(skills) == 1
    assert skills[0]["name"] == "test_skill"
    assert skills[0]["version"] == "1.0.0"
    assert skills[0]["description"] == "A test skill"


# --- Remove ---

@pytest.mark.asyncio
async def test_remove(manager: SkillManager, skill_file: Path) -> None:
    await manager.install_from_path(str(skill_file))
    assert manager.remove_skill("test_skill")
    assert not (manager.skills_dir / "test_skill.yaml").exists()


def test_remove_nonexistent(manager: SkillManager) -> None:
    assert not manager.remove_skill("nonexistent")


# --- Auto-detect install ---

@pytest.mark.asyncio
async def test_install_auto_detects_path(manager: SkillManager, skill_file: Path) -> None:
    ok, msg = await manager.install(str(skill_file))
    assert ok
