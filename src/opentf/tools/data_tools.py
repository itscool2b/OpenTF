"""Shared data tool definitions and handlers.

CSV/JSON reading, statistical analysis, sandboxed queries.
Used by MainAgent when task type is "data".
"""

from __future__ import annotations

import csv
import io
import json
import logging
import statistics as stats
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_FILE_SIZE = 10_000_000  # 10MB
MAX_SAMPLE_ROWS = 20
MAX_DISPLAY_ROWS = 50

# --- Tool definitions ---

READ_DATA_TOOL = {
    "name": "read_data",
    "description": "Read a CSV or JSON data file. Returns schema (column names/types) and sample rows.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to CSV or JSON file"},
            "max_rows": {"type": "integer", "description": "Max rows to return (default 20)", "default": 20},
        },
        "required": ["path"],
    },
}

DESCRIBE_DATA_TOOL = {
    "name": "describe_data",
    "description": "Get statistical summary of numeric columns: count, mean, median, stdev, min, max.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to data file"},
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific columns to describe (optional, defaults to all numeric)",
            },
        },
        "required": ["path"],
    },
}

QUERY_DATA_TOOL = {
    "name": "query_data",
    "description": "Run a safe Python expression on loaded data. Data available as `rows` (list of dicts).",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to data file"},
            "expression": {"type": "string", "description": "Python expression (e.g., 'len(rows)')"},
        },
        "required": ["path", "expression"],
    },
}

DATA_TOOLS = [READ_DATA_TOOL, DESCRIBE_DATA_TOOL, QUERY_DATA_TOOL]

# Allowed builtins for sandboxed eval
SAFE_BUILTINS = {
    "len": len, "sum": sum, "min": min, "max": max,
    "sorted": sorted, "reversed": reversed,
    "list": list, "dict": dict, "set": set, "tuple": tuple,
    "int": int, "float": float, "str": str, "bool": bool,
    "abs": abs, "round": round,
    "enumerate": enumerate, "zip": zip,
    "filter": filter, "map": map,
    "any": any, "all": all,
    "True": True, "False": False, "None": None,
}

# --- Helpers ---


def _load_data(path_str: str) -> list[dict[str, str]]:
    path = Path(path_str).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError(f"File too large: {path.stat().st_size} bytes (max {MAX_FILE_SIZE})")

    suffix = path.suffix.lower()
    text = path.read_text(errors="replace")

    if suffix == ".csv":
        return list(csv.DictReader(io.StringIO(text)))
    elif suffix in (".json", ".jsonl"):
        if suffix == ".jsonl":
            return [json.loads(line) for line in text.strip().splitlines() if line.strip()]
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


def _infer_type(values: list[str]) -> str:
    numeric = sum(1 for v in values[:20] if v and _is_numeric(v))
    return "numeric" if numeric > len(values) * 0.5 else "text"


def _is_numeric(v: str) -> bool:
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


# --- Handlers ---


async def handle_read_data(input_data: dict[str, Any]) -> str:
    try:
        rows = _load_data(input_data["path"])
    except Exception as exc:
        return f"Error: {exc}"

    if not rows:
        return "File is empty (no rows)."

    max_rows = min(input_data.get("max_rows", MAX_SAMPLE_ROWS), MAX_DISPLAY_ROWS)
    columns = list(rows[0].keys())
    types = {col: _infer_type([str(r.get(col, "")) for r in rows[:20]]) for col in columns}

    schema = json.dumps({"total_rows": len(rows), "columns": columns, "column_types": types}, indent=2)
    sample = json.dumps(rows[:max_rows], indent=2, default=str)
    return f"Schema:\n{schema}\n\nSample ({min(max_rows, len(rows))} of {len(rows)}):\n{sample}"


async def handle_describe_data(input_data: dict[str, Any]) -> str:
    try:
        rows = _load_data(input_data["path"])
    except Exception as exc:
        return f"Error: {exc}"

    if not rows:
        return "No data to describe."

    target = input_data.get("columns")
    all_cols = list(rows[0].keys())
    columns = [c for c in target if c in all_cols] if target else [
        c for c in all_cols if _infer_type([str(r.get(c, "")) for r in rows[:20]]) == "numeric"
    ]

    if not columns:
        return "No numeric columns found."

    summaries = {}
    for col in columns:
        values = [float(r.get(col, "")) for r in rows if _is_numeric(str(r.get(col, "")))]
        if not values:
            summaries[col] = {"count": 0}
            continue
        s: dict[str, Any] = {
            "count": len(values), "min": round(min(values), 4),
            "max": round(max(values), 4), "mean": round(stats.mean(values), 4),
            "median": round(stats.median(values), 4),
        }
        if len(values) > 1:
            s["stdev"] = round(stats.stdev(values), 4)
        summaries[col] = s

    return json.dumps(summaries, indent=2)


async def handle_query_data(input_data: dict[str, Any]) -> str:
    expression = input_data["expression"]
    blocked = ["import", "exec", "eval", "open", "__", "compile", "globals", "locals", "getattr", "setattr"]
    for pattern in blocked:
        if pattern in expression.lower():
            return f"Error: '{pattern}' not allowed"

    try:
        rows = _load_data(input_data["path"])
    except Exception as exc:
        return f"Error: {exc}"

    try:
        result = eval(expression, {"__builtins__": SAFE_BUILTINS}, {"rows": rows})  # noqa: S307
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:
        return f"Error: {exc}"


def data_handlers() -> dict:
    """Return handler dict for data tools."""
    return {
        "read_data": handle_read_data,
        "describe_data": handle_describe_data,
        "query_data": handle_query_data,
    }
