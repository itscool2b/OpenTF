"""Workspace scanner -- detects project type and builds context on startup.

Pure filesystem, no LLM calls. Runs once at startup, results cached
in ContextAgent's session state for all agents to use.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".tox", "dist", "build", ".eggs", "*.egg-info",
    ".next", ".nuxt", "target", ".cargo", "vendor",
}
MAX_TREE_LINES = 40
MAX_TREE_DEPTH = 3


@dataclass
class WorkspaceInfo:
    """Project context built from scanning the workspace directory."""

    root: Path
    language: str = "unknown"
    framework: str = "none"
    package_manager: str = "none"
    config_files: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    test_command: str = ""
    file_count: int = 0
    tree: str = ""
    summary: str = ""


def scan_workspace(root: Path | None = None) -> WorkspaceInfo:
    """Scan project directory and build workspace context.

    Detects language, framework, test command, and builds a file tree.
    Fast -- pure filesystem operations, no LLM calls.
    """
    root = (root or Path.cwd()).resolve()
    info = WorkspaceInfo(root=root)

    if not root.exists():
        return info

    # Detect project type from config files
    _detect_project_type(root, info)

    # Build directory tree
    info.tree = _build_tree(root)

    # Count files (capped to avoid slowdowns on huge repos)
    count = 0
    for p in root.rglob("*"):
        if p.is_symlink():
            continue
        if p.is_file() and not _should_skip(p.parent):
            count += 1
            if count >= 10_000:
                break
    info.file_count = count

    # Read summary from README
    info.summary = _read_summary(root, info)

    log.info(
        "Workspace scanned: %s (%s/%s, %d files)",
        root.name, info.language, info.framework, info.file_count,
    )
    return info


def _detect_project_type(root: Path, info: WorkspaceInfo) -> None:
    """Detect language, framework, and test command from config files."""
    # Python
    if (root / "pyproject.toml").exists():
        info.config_files.append("pyproject.toml")
        info.language = "python"
        info.package_manager = "pip"
        info.test_command = "pytest"
        _detect_python_framework(root, info)
    elif (root / "setup.py").exists():
        info.config_files.append("setup.py")
        info.language = "python"
        info.package_manager = "pip"
        info.test_command = "pytest"
    elif (root / "requirements.txt").exists():
        info.config_files.append("requirements.txt")
        info.language = "python"
        info.package_manager = "pip"
        info.test_command = "pytest"

    # JavaScript / TypeScript
    if (root / "package.json").exists():
        info.config_files.append("package.json")
        info.package_manager = "npm"
        if (root / "tsconfig.json").exists():
            info.config_files.append("tsconfig.json")
            info.language = "typescript"
        elif info.language == "unknown":
            info.language = "javascript"
        info.test_command = "npm test"
        _detect_js_framework(root, info)

    # Rust
    if (root / "Cargo.toml").exists():
        info.config_files.append("Cargo.toml")
        info.language = "rust"
        info.package_manager = "cargo"
        info.test_command = "cargo test"

    # Go
    if (root / "go.mod").exists():
        info.config_files.append("go.mod")
        info.language = "go"
        info.package_manager = "go"
        info.test_command = "go test ./..."

    # Makefile
    if (root / "Makefile").exists():
        info.config_files.append("Makefile")

    # Docker
    if (root / "Dockerfile").exists():
        info.config_files.append("Dockerfile")
    if (root / "docker-compose.yml").exists() or (root / "docker-compose.yaml").exists():
        info.config_files.append("docker-compose.yml")

    # Detect entry points
    for candidate in ["src/main.py", "main.py", "app.py", "index.ts", "index.js",
                       "src/index.ts", "src/index.js", "src/main.rs", "main.go", "cmd/main.go"]:
        if (root / candidate).exists():
            info.entry_points.append(candidate)


def _detect_python_framework(root: Path, info: WorkspaceInfo) -> None:
    """Detect Python framework from pyproject.toml deps."""
    try:
        text = (root / "pyproject.toml").read_text()
        text_lower = text.lower()
        if "fastapi" in text_lower:
            info.framework = "fastapi"
        elif "django" in text_lower:
            info.framework = "django"
        elif "flask" in text_lower:
            info.framework = "flask"
        elif "textual" in text_lower:
            info.framework = "textual"
    except Exception:
        pass


def _detect_js_framework(root: Path, info: WorkspaceInfo) -> None:
    """Detect JS framework from package.json deps."""
    try:
        data = json.loads((root / "package.json").read_text())
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        if "react" in deps:
            info.framework = "react"
        elif "vue" in deps:
            info.framework = "vue"
        elif "next" in deps:
            info.framework = "next"
        elif "express" in deps:
            info.framework = "express"
        elif "svelte" in deps:
            info.framework = "svelte"
    except Exception:
        pass


def _build_tree(root: Path, max_lines: int = MAX_TREE_LINES) -> str:
    """Build an indented directory tree, skipping noise."""
    lines: list[str] = []

    def walk(path: Path, prefix: str, depth: int) -> None:
        if len(lines) >= max_lines or depth > MAX_TREE_DEPTH:
            return
        if path.is_symlink():
            return

        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except PermissionError:
            return

        dirs = [e for e in entries if e.is_dir() and e.name not in SKIP_DIRS]
        files = [e for e in entries if e.is_file()]

        for f in files:
            if len(lines) >= max_lines:
                lines.append(f"{prefix}... (truncated)")
                return
            lines.append(f"{prefix}{f.name}")

        for d in dirs:
            if len(lines) >= max_lines:
                lines.append(f"{prefix}... (truncated)")
                return
            lines.append(f"{prefix}{d.name}/")
            walk(d, prefix + "  ", depth + 1)

    lines.append(f"{root.name}/")
    walk(root, "  ", 1)
    return "\n".join(lines)


def _read_summary(root: Path, info: WorkspaceInfo) -> str:
    """Build a brief project summary."""
    parts = [f"Project: {root.name}"]
    if info.language != "unknown":
        parts.append(f"Language: {info.language}")
    if info.framework != "none":
        parts.append(f"Framework: {info.framework}")

    # Try README
    for readme_name in ["README.md", "README.rst", "README.txt", "README"]:
        readme = root / readme_name
        if readme.exists():
            try:
                with readme.open("r", errors="replace") as f:
                    text = f.read(512).strip()
                if text:
                    # Take first paragraph
                    first_para = text.split("\n\n")[0].replace("\n", " ").strip()
                    if first_para:
                        parts.append(first_para)
            except Exception:
                pass
            break

    return ". ".join(parts)


def _should_skip(path: Path) -> bool:
    """Check if a path should be skipped during counting."""
    return any(part in SKIP_DIRS for part in path.parts)
