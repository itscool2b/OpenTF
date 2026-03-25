"""Skill manager: install, export, list, and remove skills.

Skills are YAML files stored in ~/.config/opentf/skills/ containing
a name, description, capabilities, system_prompt, and optional metadata
(version, author, tags, source).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

SKILLS_DIR = Path.home() / ".config" / "opentf" / "skills"
REQUIRED_FIELDS = ["name", "description", "capabilities", "system_prompt"]


class SkillManager:
    """Install, export, list, and remove skills."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self.skills_dir = skills_dir or SKILLS_DIR

    def validate(self, data: dict[str, Any]) -> tuple[bool, str]:
        """Validate skill data. Returns (valid, error_message)."""
        for field in REQUIRED_FIELDS:
            if field not in data:
                return False, f"Missing required field: {field}"
        if not isinstance(data["capabilities"], list):
            return False, "capabilities must be a list"
        name = data["name"]
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            return False, "name must be alphanumeric (underscores and hyphens allowed)"
        return True, ""

    async def install(self, source: str) -> tuple[bool, str]:
        """Install from URL or local path (auto-detect)."""
        source = source.strip()
        if source.startswith("http://") or source.startswith("https://"):
            return await self.install_from_url(source)
        return await self.install_from_path(source)

    async def install_from_path(self, path: str) -> tuple[bool, str]:
        """Install a skill from a local YAML file."""
        file_path = Path(path).resolve()
        if not file_path.exists():
            return False, f"File not found: {file_path}"
        if not file_path.suffix in (".yaml", ".yml"):
            return False, "File must be .yaml or .yml"

        try:
            data = yaml.safe_load(file_path.read_text())
        except yaml.YAMLError as exc:
            return False, f"Invalid YAML: {exc}"

        if not isinstance(data, dict):
            return False, "YAML must be a mapping"

        valid, err = self.validate(data)
        if not valid:
            return False, err

        data.setdefault("version", "1.0.0")
        data.setdefault("source", str(file_path))
        return self._save(data)

    async def install_from_url(self, url: str) -> tuple[bool, str]:
        """Install a skill from a URL."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as exc:
            return False, f"Failed to fetch: {exc}"

        try:
            data = yaml.safe_load(resp.text)
        except yaml.YAMLError as exc:
            return False, f"Invalid YAML from URL: {exc}"

        if not isinstance(data, dict):
            return False, "YAML must be a mapping"

        valid, err = self.validate(data)
        if not valid:
            return False, err

        data.setdefault("version", "1.0.0")
        data.setdefault("source", url)
        return self._save(data)

    def export_skill(self, name: str) -> str | None:
        """Export a skill as YAML string. Returns None if not found."""
        path = self.skills_dir / f"{name}.yaml"
        if not path.exists():
            return None
        return path.read_text()

    def list_skills(self) -> list[dict[str, Any]]:
        """List all installed skills with metadata."""
        if not self.skills_dir.exists():
            return []

        skills = []
        for path in sorted(self.skills_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text())
                skills.append({
                    "name": data.get("name", path.stem),
                    "description": data.get("description", ""),
                    "version": data.get("version", "?"),
                    "author": data.get("author", ""),
                    "tags": data.get("tags", []),
                    "source": data.get("source", "local"),
                })
            except Exception:
                continue
        return skills

    def remove_skill(self, name: str) -> bool:
        """Remove a skill file. Returns True if found and removed."""
        path = self.skills_dir / f"{name}.yaml"
        if path.exists():
            path.unlink()
            log.info("Removed skill: %s", name)
            return True
        return False

    def _save(self, data: dict[str, Any]) -> tuple[bool, str]:
        """Save validated skill data to skills_dir."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        name = data["name"]
        path = self.skills_dir / f"{name}.yaml"

        if path.exists():
            return False, f"Skill '{name}' already exists. Remove it first with /skill remove {name}"

        if "created_at" not in data:
            data["created_at"] = datetime.now(timezone.utc).isoformat()

        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        log.info("Installed skill: %s", name)
        return True, f"Installed skill '{name}'"
