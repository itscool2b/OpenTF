"""Repository map using tree-sitter parsing and PageRank ranking.

Inspired by Aider's repo map: parses source files with tree-sitter,
builds a definition/reference graph, ranks symbols by importance using
PageRank, and produces a token-budgeted summary for LLM context.

Falls back gracefully if tree-sitter is not installed.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Languages we support parsing
_LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

# Try to import tree-sitter
try:
    import tree_sitter_python as _tsp
    import tree_sitter_javascript as _tsjs
    from tree_sitter import Language, Parser

    _LANGUAGES: dict[str, Language] = {
        "python": Language(_tsp.language()),
        "javascript": Language(_tsjs.language()),
    }
    _HAS_TREE_SITTER = True
except ImportError:
    _HAS_TREE_SITTER = False
    _LANGUAGES = {}

# Try to import networkx
try:
    import networkx as nx
    _HAS_NETWORKX = True
except ImportError:
    _HAS_NETWORKX = False


@dataclass
class Symbol:
    """A code symbol (function, class, method) extracted from source."""
    name: str
    kind: str  # "function", "class", "method"
    line: int
    signature: str
    file_path: str


@dataclass
class FileEntry:
    """A parsed source file with its symbols."""
    path: str
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    size: int = 0
    last_modified: float = 0.0


@dataclass
class RepoIndex:
    """Complete repository index with files and dependency graph."""
    files: list[FileEntry] = field(default_factory=list)
    all_symbols: list[Symbol] = field(default_factory=list)

    @property
    def symbol_count(self) -> int:
        return len(self.all_symbols)


def is_available() -> bool:
    """Check if tree-sitter parsing is available."""
    return _HAS_TREE_SITTER


def scan(
    root: Path,
    gitignore_patterns: list[str] | None = None,
    max_files: int = 500,
) -> RepoIndex:
    """Scan a repository and extract code structure.

    Falls back to empty index if tree-sitter is not installed.
    """
    if not _HAS_TREE_SITTER:
        log.info("tree-sitter not available, returning empty repo index")
        return RepoIndex()

    index = RepoIndex()
    skip_dirs = {
        ".git", "node_modules", ".venv", "venv", "__pycache__",
        "dist", "build", ".mypy_cache", ".pytest_cache", ".tox",
        ".eggs", "*.egg-info", ".next", ".nuxt",
    }

    files_scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden and known dirs
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]

        for fname in filenames:
            if files_scanned >= max_files:
                break

            fpath = Path(dirpath) / fname
            ext = fpath.suffix.lower()
            lang = _LANGUAGE_EXTENSIONS.get(ext)
            if lang is None or lang not in _LANGUAGES:
                continue

            try:
                content = fpath.read_text(errors="replace")
                stat = fpath.stat()
            except (OSError, UnicodeDecodeError):
                continue

            symbols = _parse_file(content, lang, str(fpath.relative_to(root)))
            entry = FileEntry(
                path=str(fpath.relative_to(root)),
                language=lang,
                symbols=symbols,
                size=stat.st_size,
                last_modified=stat.st_mtime,
            )
            index.files.append(entry)
            index.all_symbols.extend(symbols)
            files_scanned += 1

    log.info("Repo map: scanned %d files, found %d symbols", len(index.files), index.symbol_count)
    return index


def rank_symbols(
    index: RepoIndex,
    query: str = "",
    chat_files: list[str] | None = None,
    top_k: int = 50,
) -> list[Symbol]:
    """Rank symbols by importance using PageRank on the reference graph.

    Falls back to simple sorting by file recency if networkx is not available.
    """
    if not index.all_symbols:
        return []

    if not _HAS_NETWORKX:
        # Fallback: sort by file recency
        file_times = {f.path: f.last_modified for f in index.files}
        return sorted(
            index.all_symbols,
            key=lambda s: file_times.get(s.file_path, 0),
            reverse=True,
        )[:top_k]

    # Build graph: nodes = files, edges = references between files
    G = nx.DiGraph()

    # Add all files as nodes
    for f in index.files:
        G.add_node(f.path)

    # Build symbol lookup: name -> list of file paths that define it
    definitions: dict[str, list[str]] = {}
    for sym in index.all_symbols:
        definitions.setdefault(sym.name, []).append(sym.file_path)

    # For each file, check which symbols from OTHER files it might reference
    # (Simple heuristic: if a file contains the name of a symbol defined elsewhere, add edge)
    file_contents: dict[str, str] = {}
    for f in index.files:
        try:
            full_path = Path.cwd() / f.path
            file_contents[f.path] = full_path.read_text(errors="replace")
        except Exception:
            file_contents[f.path] = ""

    # Pre-compile word-boundary patterns for each symbol (skip short names)
    sym_patterns: dict[str, re.Pattern[str]] = {}
    for sym_name in definitions:
        if len(sym_name) >= 3:
            sym_patterns[sym_name] = re.compile(r"\b" + re.escape(sym_name) + r"\b")

    for f_path, content in file_contents.items():
        for sym_name, def_files in definitions.items():
            if sym_name not in sym_patterns:
                continue  # Skip short symbols (i, x, id) -- too noisy
            for def_file in def_files:
                if def_file != f_path and sym_patterns[sym_name].search(content):
                    G.add_edge(f_path, def_file, weight=1.0)

    # Self-loops for isolated nodes (per Aider's approach)
    for node in G.nodes():
        if G.in_degree(node) == 0 and G.out_degree(node) == 0:
            G.add_edge(node, node, weight=0.1)

    # Personalization: boost files mentioned in chat context
    personalization = None
    if chat_files:
        personalization = {}
        for node in G.nodes():
            personalization[node] = 2.0 if node in chat_files else 1.0

    # Run PageRank
    try:
        scores = nx.pagerank(G, personalization=personalization, weight="weight")
    except Exception:
        scores = {f.path: 1.0 for f in index.files}

    # Rank symbols by their file's PageRank score
    ranked = sorted(
        index.all_symbols,
        key=lambda s: scores.get(s.file_path, 0),
        reverse=True,
    )

    # If query provided, boost symbols using semantic search + name matching
    if query:
        # Try semantic search first (embedding-based)
        semantic_scores: dict[str, float] = {}
        if _semantic_cache:
            for sym, score in semantic_search(query, embedder=None, top_k=top_k * 2):
                key = f"{sym.file_path}:{sym.name}:{sym.line}"
                semantic_scores[key] = score

        query_lower = query.lower()
        query_words = set(query_lower.split())

        def relevance(sym: Symbol) -> float:
            base = scores.get(sym.file_path, 0)
            name_lower = sym.name.lower()
            # Name matching boost
            if name_lower in query_lower:
                base *= 3.0
            elif any(w in name_lower for w in query_words):
                base *= 2.0
            # Semantic similarity boost (if available)
            key = f"{sym.file_path}:{sym.name}:{sym.line}"
            if key in semantic_scores:
                base *= (1.0 + semantic_scores[key])
            return base

        ranked = sorted(index.all_symbols, key=relevance, reverse=True)

    return ranked[:top_k]


def format_repo_map(
    index: RepoIndex,
    query: str = "",
    chat_files: list[str] | None = None,
    max_tokens: int = 1024,
) -> str:
    """Format a token-budgeted repo map for LLM system prompt injection.

    Returns a concise listing of the most important symbols.
    """
    ranked = rank_symbols(index, query, chat_files)
    if not ranked:
        return ""

    # Estimate ~4 chars per token
    max_chars = max_tokens * 4
    lines: list[str] = ["Repository map (key symbols):"]
    current_file = ""
    total_chars = len(lines[0])

    for sym in ranked:
        if sym.file_path != current_file:
            current_file = sym.file_path
            file_line = f"\n  {current_file}:"
            if total_chars + len(file_line) > max_chars:
                break
            lines.append(file_line)
            total_chars += len(file_line)

        sym_line = f"    {sym.kind} {sym.signature} (L{sym.line})"
        if total_chars + len(sym_line) > max_chars:
            break
        lines.append(sym_line)
        total_chars += len(sym_line)

    return "\n".join(lines)


# --- Tree-sitter parsing ---

def _parse_file(content: str, language: str, rel_path: str) -> list[Symbol]:
    """Parse a file with tree-sitter and extract function/class definitions."""
    if not _HAS_TREE_SITTER or language not in _LANGUAGES:
        return []

    parser = Parser(_LANGUAGES[language])
    tree = parser.parse(content.encode())

    symbols: list[Symbol] = []
    _walk_tree(tree.root_node, content, rel_path, language, symbols)
    return symbols


def _walk_tree(
    node: Any, content: str, rel_path: str, language: str,
    symbols: list[Symbol], depth: int = 0,
) -> None:
    """Walk the AST and extract definitions."""
    if depth > 10:
        return

    node_type = node.type

    # Python
    if language == "python":
        if node_type == "function_definition":
            name = _get_child_text(node, "name", content)
            sig = _get_line(content, node.start_point[0])
            symbols.append(Symbol(name=name, kind="function", line=node.start_point[0] + 1, signature=sig.strip(), file_path=rel_path))
        elif node_type == "class_definition":
            name = _get_child_text(node, "name", content)
            sig = _get_line(content, node.start_point[0])
            symbols.append(Symbol(name=name, kind="class", line=node.start_point[0] + 1, signature=sig.strip(), file_path=rel_path))

    # JavaScript/TypeScript
    elif language in ("javascript", "typescript"):
        if node_type == "function_declaration":
            name = _get_child_text(node, "name", content)
            sig = _get_line(content, node.start_point[0])
            symbols.append(Symbol(name=name, kind="function", line=node.start_point[0] + 1, signature=sig.strip(), file_path=rel_path))
        elif node_type == "class_declaration":
            name = _get_child_text(node, "name", content)
            sig = _get_line(content, node.start_point[0])
            symbols.append(Symbol(name=name, kind="class", line=node.start_point[0] + 1, signature=sig.strip(), file_path=rel_path))
        elif node_type == "method_definition":
            name = _get_child_text(node, "name", content)
            sig = _get_line(content, node.start_point[0])
            symbols.append(Symbol(name=name, kind="method", line=node.start_point[0] + 1, signature=sig.strip(), file_path=rel_path))

    for child in node.children:
        _walk_tree(child, content, rel_path, language, symbols, depth + 1)


def _get_child_text(node: Any, field_name: str, content: str) -> str:
    """Get the text of a named child node."""
    for child in node.children:
        if child.type == field_name or (hasattr(node, 'child_by_field_name') and node.child_by_field_name(field_name) == child):
            return content[child.start_byte:child.end_byte]
    # Fallback: try field access
    try:
        child = node.child_by_field_name(field_name)
        if child:
            return content[child.start_byte:child.end_byte]
    except Exception:
        pass
    return "<unknown>"


def _get_line(content: str, line_num: int) -> str:
    """Get a specific line from content by 0-indexed line number."""
    lines = content.splitlines()
    if 0 <= line_num < len(lines):
        return lines[line_num]
    return ""


# --- Semantic indexing (embedding-based search) ---

_semantic_cache: dict[str, list[float]] = {}
_semantic_keys: list[str] = []
_semantic_symbols: list[Symbol] = []


def build_semantic_index(index: RepoIndex, embedder: Any) -> int:
    """Build embedding vectors for each symbol's signature.

    Uses the provided SentenceTransformer embedder to create vectors
    for semantic search. Returns the number of symbols indexed.

    Args:
        index: The RepoIndex with parsed symbols
        embedder: A SentenceTransformer model (e.g., all-MiniLM-L6-v2)
    """
    global _semantic_cache, _semantic_keys, _semantic_symbols

    if not index.all_symbols:
        return 0

    texts: list[str] = []
    keys: list[str] = []
    symbols: list[Symbol] = []

    for sym in index.all_symbols:
        text = f"{sym.file_path}: {sym.kind} {sym.signature}"
        key = f"{sym.file_path}:{sym.name}:{sym.line}"
        texts.append(text)
        keys.append(key)
        symbols.append(sym)

    try:
        vectors = embedder.encode(texts)
        _semantic_cache = {k: v.tolist() for k, v in zip(keys, vectors)}
        _semantic_keys = keys
        _semantic_symbols = symbols
        return len(texts)
    except Exception as exc:
        log.warning("Failed to build semantic index: %s", exc)
        return 0


def semantic_search(
    query: str,
    embedder: Any,
    top_k: int = 20,
) -> list[tuple[Symbol, float]]:
    """Search the semantic index for symbols matching a query.

    Returns list of (Symbol, score) tuples sorted by relevance.
    """
    if not _semantic_cache or not _semantic_keys:
        return []

    try:
        import numpy as np

        query_vec = embedder.encode([query])[0]
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        results: list[tuple[Symbol, float]] = []
        for i, key in enumerate(_semantic_keys):
            if key not in _semantic_cache:
                continue
            sym_vec = np.array(_semantic_cache[key])
            sym_norm = np.linalg.norm(sym_vec)
            if sym_norm == 0:
                continue
            cosine = float(np.dot(query_vec, sym_vec) / (query_norm * sym_norm))
            results.append((_semantic_symbols[i], cosine))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
    except ImportError:
        log.debug("numpy not available for semantic search")
        return []
    except Exception as exc:
        log.warning("Semantic search failed: %s", exc)
        return []


def invalidate_semantic_cache() -> None:
    """Invalidate the semantic cache (e.g., after file changes)."""
    global _semantic_cache, _semantic_keys, _semantic_symbols
    _semantic_cache.clear()
    _semantic_keys.clear()
    _semantic_symbols.clear()
