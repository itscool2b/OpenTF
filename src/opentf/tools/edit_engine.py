"""Multi-strategy edit matching engine matching OpenCode's 9-strategy pipeline.

Strategies (tried in order, stops at first success):
1. Exact match (str.count / str.replace)
2. Line-trimmed match (strip each line's leading/trailing whitespace)
3. Whitespace-normalized match (strip trailing, normalize indent, collapse blanks)
4. Indentation-flexible match (re-indent old_text to match content's indentation)
5. Escape-normalized match (normalize escape sequences and quotes)
6. Block-anchor match (match first+last N lines, infer the block between)
7. Levenshtein match (character-level edit distance with sliding window)
8. Fuzzy match via SequenceMatcher (line-level sliding window)
9. Line-range fallback (explicit line numbers when all else fails)
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MatchResult:
    """Result of a successful match in the edit engine."""
    start_idx: int
    end_idx: int
    matched_text: str
    strategy: str
    confidence: float  # 0.0 - 1.0


class EditEngine:
    """9-strategy text matching for file edits, matching OpenCode's pipeline."""

    def __init__(self, fuzzy_threshold: float = 0.85) -> None:
        self.fuzzy_threshold = fuzzy_threshold

    def find_match(
        self,
        content: str,
        old_text: str,
        threshold: float | None = None,
    ) -> MatchResult | None:
        """Find old_text in content using 8 layered matching strategies.

        Returns MatchResult on success, None if no match above threshold.
        """
        thresh = threshold if threshold is not None else self.fuzzy_threshold

        # Strategy 1: Exact match
        result = self._exact_match(content, old_text)
        if result is not None:
            return result

        # Strategy 2: Line-trimmed match
        result = self._line_trimmed_match(content, old_text)
        if result is not None:
            return result

        # Strategy 3: Whitespace-normalized match
        result = self._normalized_match(content, old_text)
        if result is not None:
            return result

        # Strategy 4: Indentation-flexible match
        result = self._indentation_flexible_match(content, old_text)
        if result is not None:
            return result

        # Strategy 5: Escape-normalized match
        result = self._escape_normalized_match(content, old_text)
        if result is not None:
            return result

        # Strategy 6: Block-anchor match
        result = self._block_anchor_match(content, old_text)
        if result is not None:
            return result

        # Strategy 7: Levenshtein match
        result = self._levenshtein_match(content, old_text, thresh)
        if result is not None:
            return result

        # Strategy 8: Fuzzy match via SequenceMatcher
        result = self._fuzzy_match(content, old_text, thresh)
        if result is not None:
            return result

        return None

    def apply_edit(
        self,
        content: str,
        old_text: str,
        new_text: str,
        threshold: float | None = None,
        line_range: str | None = None,
    ) -> tuple[str, MatchResult | None, str]:
        """Apply an edit to content. Returns (new_content, match_result, message)."""
        # Strategy 9: Line-range mode
        if line_range:
            return self._apply_line_range(content, new_text, line_range)

        match = self.find_match(content, old_text, threshold)
        if match is None:
            error = self._build_no_match_error(content, old_text)
            return content, None, error

        new_content = content[:match.start_idx] + new_text + content[match.end_idx:]
        diff = len(new_text) - len(match.matched_text)
        sign = "+" if diff >= 0 else ""

        msg = f"{sign}{diff} chars"
        if match.strategy != "exact":
            msg += f" (matched via {match.strategy}, {match.confidence:.0%} confidence)"

        return new_content, match, msg

    def count_matches(self, content: str, old_text: str) -> int:
        """Count exact occurrences of old_text in content."""
        return content.count(old_text)

    def get_match_context(
        self, content: str, old_text: str, context_lines: int = 3
    ) -> list[str]:
        """Get surrounding context for each occurrence for disambiguation."""
        lines = content.splitlines(keepends=True)
        joined = "".join(lines)
        contexts: list[str] = []
        start = 0
        occurrence = 0

        while True:
            idx = joined.find(old_text, start)
            if idx == -1:
                break
            occurrence += 1
            line_start = joined[:idx].count("\n")
            line_end = joined[:idx + len(old_text)].count("\n")
            ctx_start = max(0, line_start - context_lines)
            ctx_end = min(len(lines), line_end + context_lines + 1)
            ctx_lines = lines[ctx_start:ctx_end]
            header = f"--- Occurrence {occurrence} (lines {ctx_start + 1}-{ctx_end}) ---"
            contexts.append(header + "\n" + "".join(ctx_lines))
            start = idx + 1

        return contexts

    # === Strategy implementations ===

    def _exact_match(self, content: str, old_text: str) -> MatchResult | None:
        """Strategy 1: Exact string match. Must appear exactly once."""
        count = content.count(old_text)
        if count != 1:
            return None
        idx = content.index(old_text)
        return MatchResult(idx, idx + len(old_text), old_text, "exact", 1.0)

    def _line_trimmed_match(self, content: str, old_text: str) -> MatchResult | None:
        """Strategy 2: Strip each line's leading/trailing whitespace, then match.

        Catches: trailing spaces, inconsistent leading whitespace on individual lines.
        """
        content_lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        if not old_lines:
            return None

        trimmed_old = [l.strip() for l in old_lines]
        trimmed_old_str = "\n".join(trimmed_old)

        # Search for the trimmed sequence in trimmed content
        trimmed_content = [l.rstrip("\n\r").strip() for l in content_lines]

        for start in range(len(trimmed_content) - len(trimmed_old) + 1):
            chunk = trimmed_content[start : start + len(trimmed_old)]
            if chunk == trimmed_old:
                # Found match at line `start`
                orig_start = sum(len(l) for l in content_lines[:start])
                orig_end = sum(len(l) for l in content_lines[: start + len(trimmed_old)])
                matched = content[orig_start:orig_end]
                return MatchResult(orig_start, orig_end, matched, "line-trimmed", 0.97)

        return None

    def _normalized_match(self, content: str, old_text: str) -> MatchResult | None:
        """Strategy 3: Whitespace-normalized match."""
        norm_content = _normalize_ws(content)
        norm_old = _normalize_ws(old_text)

        if not norm_old:
            return None

        count = norm_content.count(norm_old)
        if count != 1:
            return None

        norm_idx = norm_content.index(norm_old)
        orig_start, orig_end = _map_normalized_to_original(
            content, norm_content, norm_idx, norm_idx + len(norm_old)
        )

        if orig_start is None:
            return None

        return MatchResult(
            orig_start, orig_end, content[orig_start:orig_end],
            "whitespace-normalized", 0.95,
        )

    def _indentation_flexible_match(self, content: str, old_text: str) -> MatchResult | None:
        """Strategy 4: Re-indent old_text to match content's indentation at each position.

        Catches: 2-space vs 4-space indentation, tab vs space differences.
        """
        content_lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        if not old_lines:
            return None

        # Detect old_text's base indentation
        old_stripped = [l.rstrip("\n\r") for l in old_lines]
        old_indent = _detect_indent(old_stripped)

        for start in range(len(content_lines) - len(old_lines) + 1):
            chunk_lines = content_lines[start : start + len(old_lines)]
            content_indent = _detect_indent([l.rstrip("\n\r") for l in chunk_lines])

            # Re-indent old_text to match content's indentation
            reindented = _reindent(old_stripped, old_indent, content_indent)
            chunk_stripped = [l.rstrip("\n\r") for l in chunk_lines]

            if reindented == chunk_stripped:
                orig_start = sum(len(l) for l in content_lines[:start])
                orig_end = sum(len(l) for l in content_lines[: start + len(old_lines)])
                return MatchResult(
                    orig_start, orig_end, content[orig_start:orig_end],
                    "indentation-flexible", 0.93,
                )

        return None

    def _escape_normalized_match(self, content: str, old_text: str) -> MatchResult | None:
        """Strategy 5: Normalize escape sequences and quote styles.

        Catches: LLM outputs \\n when file has actual newlines, quote style mismatches.
        """
        norm_content = _normalize_escapes(content)
        norm_old = _normalize_escapes(old_text)

        if not norm_old or norm_content == content:
            # No normalization happened, skip (already tried exact)
            return None

        count = norm_content.count(norm_old)
        if count != 1:
            return None

        # Find in normalized, map back to original (line-level)
        norm_idx = norm_content.index(norm_old)
        # Simple char-position mapping (escapes don't change line count)
        content_lines = content.splitlines(keepends=True)
        norm_lines = norm_content.splitlines(keepends=True)

        # Find line range in normalized
        char_count = 0
        start_line = 0
        for i, line in enumerate(norm_lines):
            if char_count + len(line) > norm_idx:
                start_line = i
                break
            char_count += len(line)

        end_char = norm_idx + len(norm_old)
        char_count = 0
        end_line = start_line
        for i, line in enumerate(norm_lines):
            char_count += len(line)
            if char_count >= end_char:
                end_line = i + 1
                break

        if end_line > len(content_lines):
            return None

        orig_start = sum(len(l) for l in content_lines[:start_line])
        orig_end = sum(len(l) for l in content_lines[:end_line])

        return MatchResult(
            orig_start, orig_end, content[orig_start:orig_end],
            "escape-normalized", 0.90,
        )

    def _block_anchor_match(self, content: str, old_text: str) -> MatchResult | None:
        """Strategy 6: Match first and last N lines as anchors, infer block between.

        Catches: LLM gets the boundaries right but middle slightly wrong.
        """
        old_lines = old_text.splitlines()
        if len(old_lines) < 4:
            return None  # Need enough lines for anchors

        anchor_size = min(2, len(old_lines) // 3)
        head_anchor = [l.strip() for l in old_lines[:anchor_size]]
        tail_anchor = [l.strip() for l in old_lines[-anchor_size:]]

        content_lines = content.splitlines(keepends=True)
        content_stripped = [l.rstrip("\n\r").strip() for l in content_lines]

        # Find head anchor
        for start in range(len(content_stripped) - len(old_lines) + 1):
            if content_stripped[start : start + anchor_size] == head_anchor:
                # Check tail anchor at expected position
                expected_end = start + len(old_lines)
                if expected_end > len(content_stripped):
                    continue
                actual_tail = content_stripped[expected_end - anchor_size : expected_end]
                if actual_tail == tail_anchor:
                    orig_start = sum(len(l) for l in content_lines[:start])
                    orig_end = sum(len(l) for l in content_lines[:expected_end])
                    return MatchResult(
                        orig_start, orig_end, content[orig_start:orig_end],
                        "block-anchor", 0.88,
                    )

        return None

    def _levenshtein_match(
        self, content: str, old_text: str, threshold: float,
    ) -> MatchResult | None:
        """Strategy 7: Sliding window with Levenshtein edit distance.

        Better than SequenceMatcher for small character-level differences.
        Threshold: max edit distance = 15% of old_text length.
        """
        content_lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        old_len = len(old_lines)

        if old_len == 0 or old_len > 50:  # Skip for very large blocks (perf)
            return None

        max_dist = max(3, int(len(old_text) * 0.15))
        best_dist = max_dist + 1
        best_start = 0
        best_chunk = ""

        for start in range(len(content_lines) - old_len + 1):
            chunk = "".join(content_lines[start : start + old_len])
            dist = _fast_levenshtein(old_text, chunk, max_dist)
            if dist < best_dist:
                best_dist = dist
                best_start = start
                best_chunk = chunk

        if best_dist > max_dist:
            return None

        confidence = 1.0 - (best_dist / max(len(old_text), 1))
        if confidence < threshold:
            return None

        orig_start = sum(len(l) for l in content_lines[:best_start])
        orig_end = orig_start + len(best_chunk)

        return MatchResult(
            orig_start, orig_end, best_chunk,
            "levenshtein", confidence,
        )

    def _fuzzy_match(
        self, content: str, old_text: str, threshold: float,
    ) -> MatchResult | None:
        """Strategy 8: SequenceMatcher fuzzy match with sliding window."""
        content_lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        old_len = len(old_lines)

        if old_len == 0:
            return None

        variance = max(1, int(old_len * 0.1))
        min_chunk = max(1, old_len - variance)
        max_chunk = old_len + variance

        best_ratio = 0.0
        best_start_line = 0
        best_chunk = ""

        for chunk_size in range(min_chunk, max_chunk + 1):
            for start in range(len(content_lines) - chunk_size + 1):
                chunk_text = "".join(content_lines[start : start + chunk_size])
                ratio = difflib.SequenceMatcher(None, old_text, chunk_text).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start_line = start
                    best_chunk = chunk_text

        if best_ratio < threshold:
            return None

        start_idx = len("".join(content_lines[:best_start_line]))
        end_idx = start_idx + len(best_chunk)

        return MatchResult(start_idx, end_idx, best_chunk, "fuzzy", best_ratio)

    def _apply_line_range(
        self, content: str, new_text: str, line_range: str,
    ) -> tuple[str, MatchResult | None, str]:
        """Strategy 9: Line-range replacement (e.g. "10-25")."""
        match = re.match(r"(\d+)\s*-\s*(\d+)", line_range)
        if not match:
            return content, None, f"Error: invalid line_range format '{line_range}'. Use 'START-END' (e.g. '10-25')."

        start_line = int(match.group(1))
        end_line = int(match.group(2))
        lines = content.splitlines(keepends=True)
        total = len(lines)

        if start_line < 1 or end_line > total or start_line > end_line:
            return content, None, f"Error: line_range {start_line}-{end_line} out of bounds (file has {total} lines)."

        s = start_line - 1
        e = end_line
        old_chunk = "".join(lines[s:e])
        start_idx = len("".join(lines[:s]))
        end_idx = start_idx + len(old_chunk)

        if new_text and not new_text.endswith("\n"):
            new_text += "\n"

        new_content = "".join(lines[:s]) + new_text + "".join(lines[e:])
        result = MatchResult(start_idx, end_idx, old_chunk, "line-range", 1.0)
        replaced = end_line - start_line + 1
        return new_content, result, f"Replaced lines {start_line}-{end_line} ({replaced} lines)"

    def _build_no_match_error(self, content: str, old_text: str) -> str:
        """Build a helpful error message when no match is found."""
        exact_count = content.count(old_text)

        if exact_count > 1:
            contexts = self.get_match_context(content, old_text)
            ctx_display = "\n\n".join(contexts[:5])
            return (
                f"Error: old_text appears {exact_count} times. "
                f"Provide more surrounding context to disambiguate.\n\n"
                f"{ctx_display}"
            )

        lines = content.splitlines()
        old_first_line = old_text.splitlines()[0].strip() if old_text.strip() else ""

        if old_first_line:
            close = difflib.get_close_matches(old_first_line, [l.strip() for l in lines], n=1, cutoff=0.6)
            if close:
                return (
                    f"Error: old_text not found in file. "
                    f"Closest line found: '{close[0]}'. "
                    f"Check whitespace, indentation, or try a shorter, unique snippet."
                )

        return (
            "Error: old_text not found in file. "
            "Make sure the text matches the file content, including whitespace and indentation. "
            "You can also use line_range (e.g. '10-25') as a fallback."
        )


# === Utility functions ===


def _normalize_ws(text: str) -> str:
    """Normalize whitespace: strip trailing, normalize line endings, collapse blanks, tabs to spaces."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized: list[str] = []
    prev_blank = False
    for line in lines:
        stripped = line.rstrip().replace("\t", "    ")
        if not stripped:
            if not prev_blank:
                normalized.append("")
            prev_blank = True
        else:
            normalized.append(stripped)
            prev_blank = False
    return "\n".join(normalized)


def _normalize_escapes(text: str) -> str:
    """Normalize escape sequences and quote styles for comparison."""
    result = text.replace("\r\n", "\n").replace("\r", "\n")
    # Normalize common escape sequences that LLMs get wrong
    result = result.replace("\\n", "\n").replace("\\t", "\t")
    result = result.replace('\\"', '"').replace("\\'", "'")
    return result


def _detect_indent(lines: list[str]) -> str:
    """Detect the indentation unit used in a block of lines."""
    for line in lines:
        if line and not line.isspace():
            stripped = line.lstrip()
            indent = line[: len(line) - len(stripped)]
            if indent:
                return indent
    return ""


def _reindent(lines: list[str], old_indent: str, new_indent: str) -> list[str]:
    """Re-indent lines from old_indent level to new_indent level."""
    if not old_indent:
        return lines

    result: list[str] = []
    for line in lines:
        if line.startswith(old_indent):
            result.append(new_indent + line[len(old_indent):])
        elif line.strip() == "":
            result.append(line)
        else:
            result.append(line)
    return result


def _fast_levenshtein(s1: str, s2: str, max_dist: int) -> int:
    """Compute Levenshtein distance with early termination at max_dist.

    Returns the edit distance, or max_dist + 1 if exceeded.
    """
    if abs(len(s1) - len(s2)) > max_dist:
        return max_dist + 1

    if len(s1) > len(s2):
        s1, s2 = s2, s1

    # Use two-row optimization
    prev = list(range(len(s1) + 1))
    curr = [0] * (len(s1) + 1)

    for j in range(1, len(s2) + 1):
        curr[0] = j
        row_min = j
        for i in range(1, len(s1) + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[i] = min(curr[i - 1] + 1, prev[i] + 1, prev[i - 1] + cost)
            row_min = min(row_min, curr[i])
        if row_min > max_dist:
            return max_dist + 1
        prev, curr = curr, prev

    return prev[len(s1)]


def _map_normalized_to_original(
    original: str, normalized: str, norm_start: int, norm_end: int,
) -> tuple[int | None, int | None]:
    """Map character positions from normalized text back to original text."""
    orig_lines = original.splitlines(keepends=True)
    norm_lines = normalized.split("\n")

    norm_offsets: list[int] = []
    pos = 0
    for line in norm_lines:
        norm_offsets.append(pos)
        pos += len(line) + 1

    norm_start_line = 0
    norm_end_line = 0
    cum = 0
    for i, line in enumerate(norm_lines):
        next_cum = cum + len(line) + 1
        if cum <= norm_start < next_cum:
            norm_start_line = i
        if cum < norm_end <= next_cum:
            norm_end_line = i
            break
        cum = next_cum

    if norm_start_line >= len(orig_lines) or norm_end_line >= len(orig_lines):
        return None, None

    orig_start = sum(len(l) for l in orig_lines[:norm_start_line])
    orig_end = sum(len(l) for l in orig_lines[: norm_end_line + 1])

    return orig_start, orig_end
