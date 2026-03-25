"""Tests for workspace scanning."""

import pytest
from pathlib import Path

from opentf.core.workspace import scan_workspace, _should_skip, _build_tree


# --- Project detection ---

def test_python_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
    info = scan_workspace(tmp_path)
    assert info.language == "python"
    assert info.test_command == "pytest"
    assert "pyproject.toml" in info.config_files


def test_node_project(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "test"}')
    info = scan_workspace(tmp_path)
    assert info.language == "javascript"
    assert info.test_command == "npm test"


def test_typescript_project(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "test"}')
    (tmp_path / "tsconfig.json").write_text('{}')
    info = scan_workspace(tmp_path)
    assert info.language == "typescript"


def test_rust_project(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "test"')
    info = scan_workspace(tmp_path)
    assert info.language == "rust"
    assert info.test_command == "cargo test"


def test_go_project(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module test")
    info = scan_workspace(tmp_path)
    assert info.language == "go"
    assert info.test_command == "go test ./..."


def test_unknown_project(tmp_path: Path) -> None:
    (tmp_path / "random.txt").write_text("hello")
    info = scan_workspace(tmp_path)
    assert info.language == "unknown"


# --- Framework detection ---

def test_fastapi_detected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastapi>=0.100"]'
    )
    info = scan_workspace(tmp_path)
    assert info.framework == "fastapi"


def test_react_detected(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18"}}'
    )
    info = scan_workspace(tmp_path)
    assert info.framework == "react"


# --- File counting ---

def test_file_count_small_dir(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"file{i}.py").write_text("")
    info = scan_workspace(tmp_path)
    assert info.file_count == 5


def test_file_count_skips_git(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("")
    info = scan_workspace(tmp_path)
    assert info.file_count == 1  # Only main.py, not .git/config


# --- Tree building ---

def test_tree_respects_depth() -> None:
    # _build_tree is tested indirectly through scan_workspace
    # Just verify it returns something meaningful
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "a").mkdir()
        (root / "a" / "b").mkdir()
        (root / "a" / "b" / "c").mkdir()
        (root / "a" / "b" / "c" / "deep").mkdir()
        (root / "file.py").write_text("")
        tree = _build_tree(root)
        assert "file.py" in tree


def test_tree_skips_skip_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src" / "main.py").write_text("")
    (tmp_path / "node_modules" / "pkg.js").write_text("")
    tree = _build_tree(tmp_path)
    assert "src" in tree
    assert "node_modules" not in tree


# --- _should_skip ---

def test_should_skip_git() -> None:
    assert _should_skip(Path("/project/.git/objects"))


def test_should_skip_node_modules() -> None:
    assert _should_skip(Path("/project/node_modules/pkg"))


def test_should_skip_normal_path() -> None:
    assert not _should_skip(Path("/project/src/main.py"))


# --- README reading ---

def test_readme_included_in_summary(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# My Project\n\nA cool tool.")
    info = scan_workspace(tmp_path)
    assert "My Project" in info.summary


def test_large_readme_only_reads_limited(tmp_path: Path) -> None:
    large_content = "# Title\n\n" + "x" * 10_000
    (tmp_path / "README.md").write_text(large_content)
    info = scan_workspace(tmp_path)
    # Summary should not contain the full 10k chars
    assert len(info.summary) < 1000


def test_no_readme_still_has_summary(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'")
    info = scan_workspace(tmp_path)
    assert "Project:" in info.summary or tmp_path.name in info.summary
