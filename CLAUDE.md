# OpenTF (Open Task Force)

## Python Environment

Always use a virtual environment. Activate `.venv` before running any Python commands (pip, python, pytest, etc.). If `.venv` doesn't exist, create it with `python -m venv .venv` first.

## Documentation

Document absolutely everything you do in this file. Every change, every decision, every new module — keep this file up to date.

## Debugging

When debugging, look for ALL edge cases before writing a fix. Test the fix until it actually works — do not move on until verified. Always choose the best solution, not the quickest hack.

## Project Structure

- Source code lives in `src/opentf/`
- Tests live in `tests/`
- Config files live in `config/`
- Docs live in `docs/`

## Running

```bash
source .venv/bin/activate
pip install -e ".[dev]"
opentf
```

## Testing

```bash
pytest
```

## Code Style

- Python 3.12+
- Type hints on all function signatures
- Pydantic models for all data structures
- Async-first (use `async def` by default)
- No emojis in code or output
