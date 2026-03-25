"""Janitor report models -- issues grouped by file with accept/reject status."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from opentf.cli.theme import COLORS


class JanitorSeverity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class JanitorIssue(BaseModel):
    """A single issue found during a janitor scan."""

    id: str                          # "J-1", "J-2", ...
    file_path: str                   # relative path
    line_start: int
    line_end: int
    severity: JanitorSeverity
    category: str                    # e.g. "dead_code", "missing_guard"
    group: str                       # "quality" or "slop"
    description: str
    snippet: str = ""
    suggested_fix: str = ""
    status: str = "pending"          # "pending", "accepted", "rejected"

    def summary_line(self) -> str:
        D = COLORS['text_dim']
        sev = self.severity.value.upper()
        loc = f"Line {self.line_start}" if self.line_start == self.line_end else f"Lines {self.line_start}-{self.line_end}"
        return f"[bold]{self.id}[/] {sev:<4} [{D}]{self.group}/{self.category}[/] {loc}\n    {self.description}"

    def detail_block(self) -> str:
        D = COLORS['text_dim']
        lines = [self.summary_line()]
        if self.snippet:
            lines.append(f"\n    [{D}]Code:[/]\n    {self.snippet}")
        if self.suggested_fix:
            lines.append(f"\n    [{D}]Fix:[/] {self.suggested_fix}")
        lines.append(f"\n    [{D}]Status:[/] {self.status}")
        return "\n".join(lines)


class JanitorReport(BaseModel):
    """Complete janitor scan report."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    issues: list[JanitorIssue] = []
    files_scanned: list[str] = []
    token_usage: int = 0
    custom_rules_loaded: bool = False

    def issues_by_file(self) -> dict[str, list[JanitorIssue]]:
        by_file: dict[str, list[JanitorIssue]] = {}
        for issue in self.issues:
            by_file.setdefault(issue.file_path, []).append(issue)
        return by_file

    def accepted_issues(self) -> list[JanitorIssue]:
        return [i for i in self.issues if i.status == "accepted"]

    def find_issue(self, num: int) -> JanitorIssue | None:
        target = f"J-{num}"
        for issue in self.issues:
            if issue.id == target:
                return issue
        return None

    def summary_line(self) -> str:
        high = sum(1 for i in self.issues if i.severity == JanitorSeverity.HIGH)
        med = sum(1 for i in self.issues if i.severity == JanitorSeverity.MEDIUM)
        low = sum(1 for i in self.issues if i.severity == JanitorSeverity.LOW)
        return f"Scanned {len(self.files_scanned)} files | {len(self.issues)} issues ({high} high, {med} medium, {low} low)"

    def format_markdown(self) -> str:
        A = COLORS['accent']
        B = COLORS['border']
        D = COLORS['text_dim']
        lines = [
            f"[bold {A}]{'─' * 40}[/]",
            f"  [bold]Janitor Report[/]",
            f"  [{D}]{self.summary_line_plain()}[/]",
            f"[{B}]{'─' * 40}[/]",
        ]

        for file_path, file_issues in self.issues_by_file().items():
            lines.append(f"\n  [bold]{file_path}[/]")
            for issue in file_issues:
                sev = issue.severity.value.upper()
                loc = f"L{issue.line_start}" if issue.line_start == issue.line_end else f"L{issue.line_start}-{issue.line_end}"
                status_icon = {"pending": "( )", "accepted": "(x)", "rejected": "(-)"}
                icon = status_icon.get(issue.status, "( )")
                lines.append(
                    f"    {icon} [bold]{issue.id}[/] {sev} [{D}]{issue.group}/{issue.category}[/] {loc}\n"
                    f"         {issue.description}\n"
                    f"         [{D}]Fix: {issue.suggested_fix}[/]"
                )

        return "\n".join(lines)

    def summary_line_plain(self) -> str:
        """Plain text summary (no markup) for use inside markup blocks."""
        high = sum(1 for i in self.issues if i.severity == JanitorSeverity.HIGH)
        med = sum(1 for i in self.issues if i.severity == JanitorSeverity.MEDIUM)
        low = sum(1 for i in self.issues if i.severity == JanitorSeverity.LOW)
        return f"{len(self.files_scanned)} files, {len(self.issues)} issues ({high} high, {med} medium, {low} low)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "issues": [
                {
                    "id": i.id,
                    "file_path": i.file_path,
                    "line_start": i.line_start,
                    "line_end": i.line_end,
                    "severity": i.severity.value,
                    "category": i.category,
                    "group": i.group,
                    "description": i.description,
                    "snippet": i.snippet,
                    "suggested_fix": i.suggested_fix,
                    "status": i.status,
                }
                for i in self.issues
            ],
            "files_scanned": self.files_scanned,
            "token_usage": self.token_usage,
            "custom_rules_loaded": self.custom_rules_loaded,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JanitorReport:
        issues = []
        for d in data.get("issues", []):
            issues.append(JanitorIssue(
                id=d["id"],
                file_path=d["file_path"],
                line_start=d["line_start"],
                line_end=d["line_end"],
                severity=JanitorSeverity(d["severity"]),
                category=d["category"],
                group=d["group"],
                description=d["description"],
                snippet=d.get("snippet", ""),
                suggested_fix=d.get("suggested_fix", ""),
                status=d.get("status", "pending"),
            ))
        return cls(
            id=data.get("id", uuid.uuid4().hex),
            issues=issues,
            files_scanned=data.get("files_scanned", []),
            token_usage=data.get("token_usage", 0),
            custom_rules_loaded=data.get("custom_rules_loaded", False),
        )
