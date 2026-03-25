"""JanitorAgent: scans codebase for code quality issues and AI slop.

Two modes:
- Scan: read-only tools, produces structured JSON report
- Fix: full file tools, applies accepted fixes
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from opentf.agents.base import AgentResult, AgentRole, BaseAgent
from opentf.core.tool_loop import ToolLoop
from opentf.llm.client import LLMClient
from opentf.models.context import AgentContext
from opentf.models.janitor import JanitorIssue, JanitorReport, JanitorSeverity
from opentf.tools.file_tools import (
    ALL_TOOLS as FILE_TOOLS,
    CODE_SAFE_COMMANDS,
    DEFAULT_SAFE_COMMANDS,
    LIST_DIR_TOOL,
    READ_FILE_TOOL,
    RUN_COMMAND_TOOL,
    SEARCH_FILES_TOOL,
    all_handlers as file_handlers,
)

log = logging.getLogger(__name__)

# Read-only tools for scanning (no write_file)
SCAN_TOOLS = [READ_FILE_TOOL, LIST_DIR_TOOL, SEARCH_FILES_TOOL, RUN_COMMAND_TOOL]

JANITOR_SCAN_PROMPT = """\
You are a code janitor agent. Your job is to scan a codebase and produce a structured report of issues. You do NOT modify any files -- you only read and report.

WORKFLOW:
1. Use read_file to check if "janitor.md" exists in the project root. If it does, read it for project-specific rules.
2. Use list_directory to understand the project structure. Skip: .venv, __pycache__, .git, node_modules, dist, build, vendor, .mypy_cache, .pytest_cache, eggs, *.egg-info.
3. Use read_file to read source files. Prioritize main source directories over tests.
4. For each file, check for issues in BOTH categories below.
5. When done scanning, output your findings as a JSON array.

CATEGORY 1 -- GENERAL CODE QUALITY:
- Dead code: unreachable branches, unused functions/variables/classes
- Unused imports
- Redundant logic: duplicate conditions, unnecessary wrappers, copy-pasted blocks
- Bad naming: single letters (except loop counters), misleading names, overly generic names
- Inconsistent style: mixed conventions within the same file
- Formatting drift: inconsistent indentation, spacing, quote style
- Duplicated code: same logic in multiple places that should be extracted
- Redundant dependencies: importing a new package when one already in use does the same thing
- Overly complex functions: too many branches, too long, should be split
- Style violations against janitor.md rules (if loaded)

CATEGORY 2 -- AI SLOP DETECTION:
- Silent failures: bare except, swallowed errors, empty catch blocks, code that runs but doesn't do what it's supposed to
- Fake output: matches expected format but contains garbage or hardcoded values
- Missing null checks, guard clauses, and early returns
- Missing error/exception handling on I/O, network, or file operations
- Hallucinated function calls: calling methods that don't exist on the object or in the module
- Hallucinated imports: importing packages not in the dependency file (pyproject.toml, requirements.txt, package.json)
- Incorrect dependency ordering: using before defining, circular references
- Concurrency misuse: race conditions, missing locks, async pitfalls
- Excessive or redundant I/O: reading same file multiple times, N+1 patterns
- Security issues: string concatenation in queries, hardcoded credentials, unsafe eval/exec
- Code that removes safety checks to avoid errors instead of handling them properly
- Generic variable names that increase cognitive load (data, temp, result, x, val, info)
- Happy-path-only logic: no error handling, assumes success, no edge case coverage
- Subtle control flow errors: off-by-one, wrong operator, missing break, wrong variable in condition

OUTPUT FORMAT -- JSON array, each item:
{{
  "file_path": "relative/path.py",
  "line_start": 42,
  "line_end": 45,
  "severity": "high|medium|low",
  "category": "dead_code|unused_import|redundant_logic|bad_naming|style_violation|formatting_drift|duplicated_code|redundant_dependency|complex_function|silent_failure|fake_output|missing_guard|missing_error_handling|hallucinated_call|hallucinated_import|dependency_ordering|concurrency_issue|excessive_io|security_issue|safety_removal|generic_naming|happy_path_only|control_flow_error",
  "group": "quality|slop",
  "description": "clear explanation of the problem",
  "snippet": "the problematic code (keep short, relevant lines only)",
  "suggested_fix": "the corrected code or fix instruction"
}}

SEVERITY GUIDELINES:
- high: bugs, security issues, hallucinated calls/imports, silent failures
- medium: missing guards, dead code, redundant logic, bad naming
- low: style issues, formatting drift, generic naming

Output ONLY the JSON array. No other text before or after it.
{custom_rules}"""

JANITOR_FIX_PROMPT = """\
You are applying code fixes to a codebase. For each file listed below, do the following:

1. read_file to get the current contents
2. Apply the fixes described for that file
3. write_file with the corrected contents

RULES:
- Preserve ALL existing functionality. Only change what the fix describes.
- Do not reformat or restructure code beyond the specific fix.
- Do not add new imports unless the fix requires it.
- Do not change indentation style, quote style, or whitespace beyond the fix.
- If a fix seems risky or ambiguous, skip it and explain why.
- After each file, report what you changed in a brief summary.
- Apply all fixes for a file in a single read-write cycle."""


class JanitorAgent(BaseAgent):
    """Scans codebase for code quality issues and AI slop."""

    def __init__(self, llm: LLMClient) -> None:
        super().__init__(
            name="janitor",
            description="Scans codebase for code quality issues and AI slop",
            capabilities=["janitor"],
            role=AgentRole.SPECIALIST,
        )
        self.llm = llm

    async def process(self, context: AgentContext) -> AgentResult:
        mode = context.constraints.get("mode", "scan")
        if mode == "fix":
            return await self._apply_fixes(context)
        return await self._scan(context)

    async def _scan(self, context: AgentContext) -> AgentResult:
        """Scan the codebase with read-only tools."""
        bus = context.constraints.get("_bus")
        scope = context.constraints.get("scan_scope")

        # Load janitor.md if it exists
        custom_rules = self._load_janitor_md()

        # Build system prompt
        custom_section = ""
        if custom_rules:
            custom_section = f"\n\nPROJECT-SPECIFIC RULES (from janitor.md):\n{custom_rules}\n\nTreat violations of these rules as style_violation with medium severity."

        system = JANITOR_SCAN_PROMPT.format(custom_rules=custom_section)

        # Build scan request
        if scope:
            user_msg = f"Scan only files under `{scope}`. Focus on source files, skip generated code."
        else:
            user_msg = "Scan the codebase starting from the project root. Focus on source files, skip test files unless they have obvious issues."

        # Read-only tools
        tools = list(SCAN_TOOLS)
        handlers = file_handlers(DEFAULT_SAFE_COMMANDS)
        # Remove write_file handler if present
        handlers.pop("write_file", None)

        loop = ToolLoop(
            llm=self.llm,
            tools=tools,
            handlers=handlers,
            max_iterations=30,
            bus=bus,
            source=self.name,
        )

        messages: list[dict] = [{"role": "user", "content": user_msg}]
        text, tokens = await loop.run(
            messages=messages,
            system=system,
            temperature=0.2,
        )

        # Parse JSON output
        issues = self._parse_issues(text)

        # Collect scanned files from issue paths
        scanned_files = list({i.file_path for i in issues})
        scanned_files.sort()

        report = JanitorReport(
            issues=issues,
            files_scanned=scanned_files,
            token_usage=tokens,
            custom_rules_loaded=bool(custom_rules),
        )

        return AgentResult(
            success=True,
            output={
                "report": report.to_dict(),
                "response": report.format_markdown(),
            },
            token_usage=tokens,
        )

    async def _apply_fixes(self, context: AgentContext) -> AgentResult:
        """Apply accepted fixes using full file tools."""
        bus = context.constraints.get("_bus")
        issues_data = context.constraints.get("issues", [])

        if not issues_data:
            return AgentResult(
                success=True,
                output={"response": "No issues to fix."},
            )

        # Group by file for the fix request
        by_file: dict[str, list[dict]] = {}
        for d in issues_data:
            by_file.setdefault(d["file_path"], []).append(d)

        # Build fix request message
        fix_lines = ["Apply these fixes:\n"]
        for file_path, file_issues in by_file.items():
            fix_lines.append(f"\n## {file_path}")
            for d in file_issues:
                fix_lines.append(
                    f"{d['id']}: {d['description']}\n"
                    f"  Lines {d['line_start']}-{d['line_end']}\n"
                    f"  Fix: {d['suggested_fix']}"
                )

        user_msg = "\n".join(fix_lines)

        # Full file tools including write
        tools = list(FILE_TOOLS)
        handlers = file_handlers(CODE_SAFE_COMMANDS)

        max_iters = max(len(by_file) * 3, 10)

        loop = ToolLoop(
            llm=self.llm,
            tools=tools,
            handlers=handlers,
            max_iterations=max_iters,
            bus=bus,
            source=self.name,
        )

        text, tokens = await loop.run(
            messages=[{"role": "user", "content": user_msg}],
            system=JANITOR_FIX_PROMPT,
            temperature=0.1,
        )

        return AgentResult(
            success=True,
            output={"response": text},
            token_usage=tokens,
        )

    def _load_janitor_md(self) -> str:
        """Read janitor.md from project root. Returns empty string if missing."""
        path = Path.cwd() / "janitor.md"
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError):
            return ""

    def _parse_issues(self, text: str) -> list[JanitorIssue]:
        """Parse LLM JSON output into JanitorIssue list."""
        text = text.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to find a JSON array in the text
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    log.warning("Failed to parse janitor JSON output")
                    return []
            else:
                return []

        if not isinstance(data, list):
            return []

        valid_severities = {s.value for s in JanitorSeverity}
        issues = []
        for idx, item in enumerate(data, start=1):
            try:
                severity = item.get("severity", "medium")
                if severity not in valid_severities:
                    severity = "medium"

                issues.append(JanitorIssue(
                    id=f"J-{idx}",
                    file_path=item["file_path"],
                    line_start=item.get("line_start", 0),
                    line_end=item.get("line_end", item.get("line_start", 0)),
                    severity=JanitorSeverity(severity),
                    category=item.get("category", "unknown"),
                    group=item.get("group", "quality"),
                    description=item.get("description", ""),
                    snippet=item.get("snippet", ""),
                    suggested_fix=item.get("suggested_fix", ""),
                ))
            except (KeyError, ValueError) as exc:
                log.warning("Skipping malformed janitor issue %d: %s", idx, exc)
                continue

        return issues
