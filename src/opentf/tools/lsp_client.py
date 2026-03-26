"""LSP (Language Server Protocol) client for code diagnostics.

Inspired by OpenCode/Crush: lazy-loads language servers per file extension,
sends didChange notifications after edits, collects diagnostics (type errors,
lint issues) and feeds them back to the LLM.

The key flow:
1. After edit_file/write_file, notify the LSP server of the change
2. Wait for diagnostics (with debounce)
3. If errors found, append them to the tool result so the LLM can self-correct
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Server commands per language (auto-detected)
DEFAULT_LSP_SERVERS: dict[str, str] = {
    "python": "pyright-langserver --stdio",
    "typescript": "typescript-language-server --stdio",
    "javascript": "typescript-language-server --stdio",
}

# File extension to language mapping
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}


@dataclass
class Diagnostic:
    """A single diagnostic from an LSP server."""
    file: str
    line: int
    col: int
    severity: str  # "error", "warning", "info", "hint"
    message: str
    code: str = ""


@dataclass
class LSPServer:
    """A running LSP server process."""
    language: str
    process: asyncio.subprocess.Process
    request_id: int = 0
    opened_files: set[str] = field(default_factory=set)
    diagnostics: dict[str, list[Diagnostic]] = field(default_factory=dict)
    _reader_task: asyncio.Task | None = None


class LSPManager:
    """Manages LSP server lifecycles and diagnostics collection.

    Lazy-loads servers: only spawns when a file with matching extension is accessed.
    """

    def __init__(self, server_config: dict[str, str] | None = None) -> None:
        self._config = server_config or DEFAULT_LSP_SERVERS
        self._servers: dict[str, LSPServer] = {}
        self._available: dict[str, bool] = {}

    def is_available(self, language: str) -> bool:
        """Check if an LSP server is available for this language."""
        if language in self._available:
            return self._available[language]

        cmd = self._config.get(language, "")
        if not cmd:
            self._available[language] = False
            return False

        binary = cmd.split()[0]
        self._available[language] = shutil.which(binary) is not None
        return self._available[language]

    def language_for_file(self, file_path: str) -> str | None:
        """Get the language for a file path."""
        ext = Path(file_path).suffix.lower()
        return _EXT_TO_LANG.get(ext)

    async def get_diagnostics(self, file_path: str) -> list[Diagnostic]:
        """Get diagnostics for a file from the appropriate LSP server.

        Launches the server if not running. Sends didOpen/didChange and
        waits briefly for diagnostics.
        """
        lang = self.language_for_file(file_path)
        if not lang or not self.is_available(lang):
            return []

        server = await self._ensure_server(lang)
        if server is None:
            return []

        # Read current file content
        try:
            content = Path(file_path).read_text(errors="replace")
        except Exception:
            return []

        abs_path = str(Path(file_path).resolve())
        uri = f"file://{abs_path}"

        if abs_path not in server.opened_files:
            # Send textDocument/didOpen
            await self._notify(server, "textDocument/didOpen", {
                "textDocument": {
                    "uri": uri,
                    "languageId": lang,
                    "version": 1,
                    "text": content,
                },
            })
            server.opened_files.add(abs_path)
        else:
            # Send textDocument/didChange
            await self._notify(server, "textDocument/didChange", {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": content}],
            })

        # Wait for diagnostics (debounce 300ms)
        await asyncio.sleep(0.3)

        return server.diagnostics.get(abs_path, [])

    async def collect_diagnostics_after_edit(self, file_path: str) -> str | None:
        """Collect diagnostics after a file edit. Returns formatted string or None.

        This is the main integration point: called after edit_file/write_file
        to provide immediate type/lint feedback to the LLM.
        """
        diagnostics = await self.get_diagnostics(file_path)
        errors = [d for d in diagnostics if d.severity == "error"]

        if not errors:
            return None

        lines = [f"LSP diagnostics ({len(errors)} errors):"]
        for d in errors[:10]:  # Cap at 10 errors
            lines.append(f"  {d.file}:{d.line}:{d.col} - {d.message}")
        return "\n".join(lines)

    async def _ensure_server(self, language: str) -> LSPServer | None:
        """Ensure an LSP server is running for the language."""
        if language in self._servers:
            server = self._servers[language]
            if server.process.returncode is None:
                return server
            # Server died, remove it and restart
            log.warning(
                "LSP server for %s died (exit %s), restarting...",
                language, server.process.returncode,
            )
            del self._servers[language]

        cmd = self._config.get(language, "")
        if not cmd:
            return None

        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            server = LSPServer(language=language, process=process)
            self._servers[language] = server

            # Start reader task
            server._reader_task = asyncio.create_task(
                self._read_responses(server)
            )

            # Send initialize request
            root_uri = f"file://{Path.cwd().resolve()}"
            await self._request(server, "initialize", {
                "processId": None,
                "rootUri": root_uri,
                "capabilities": {
                    "textDocument": {
                        "synchronization": {
                            "didOpen": True,
                            "didChange": True,
                        },
                        "publishDiagnostics": {},
                    },
                },
            })

            # Send initialized notification
            await self._notify(server, "initialized", {})

            log.info("LSP server started for %s: %s", language, cmd)
            return server

        except Exception as exc:
            log.warning("Failed to start LSP server for %s: %s", language, exc)
            self._available[language] = False
            return None

    async def _request(self, server: LSPServer, method: str, params: dict) -> Any:
        """Send a JSON-RPC request and return the result."""
        server.request_id += 1
        msg = {
            "jsonrpc": "2.0",
            "id": server.request_id,
            "method": method,
            "params": params,
        }
        await self._send(server, msg)
        # Wait briefly for response
        await asyncio.sleep(0.2)
        return None  # We don't parse responses for now

    async def _notify(self, server: LSPServer, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._send(server, msg)

    async def _send(self, server: LSPServer, msg: dict) -> None:
        """Send a JSON-RPC message to the server."""
        if server.process.stdin is None:
            return
        body = json.dumps(msg)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        try:
            server.process.stdin.write((header + body).encode())
            await server.process.stdin.drain()
        except Exception as exc:
            log.debug("Failed to send to LSP: %s", exc)

    async def _read_responses(self, server: LSPServer) -> None:
        """Background task reading LSP server responses and collecting diagnostics."""
        if server.process.stdout is None:
            return

        try:
            while server.process.returncode is None:
                # Read header
                header_line = await server.process.stdout.readline()
                if not header_line:
                    break

                header = header_line.decode(errors="replace").strip()
                if not header.startswith("Content-Length:"):
                    continue

                content_length = int(header.split(":")[1].strip())

                # Read blank line
                await server.process.stdout.readline()

                # Read body
                body = await server.process.stdout.read(content_length)
                if not body:
                    break

                try:
                    msg = json.loads(body.decode(errors="replace"))
                    self._handle_message(server, msg)
                except json.JSONDecodeError:
                    continue

        except (asyncio.CancelledError, Exception):
            pass

    def _handle_message(self, server: LSPServer, msg: dict) -> None:
        """Handle an incoming LSP message (primarily diagnostics)."""
        method = msg.get("method", "")
        if method == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            uri = params.get("uri", "")
            raw_diagnostics = params.get("diagnostics", [])

            # Convert URI to file path
            file_path = uri.replace("file://", "")

            severity_map = {1: "error", 2: "warning", 3: "info", 4: "hint"}
            diagnostics = []
            for d in raw_diagnostics:
                rng = d.get("range", {}).get("start", {})
                diagnostics.append(Diagnostic(
                    file=file_path,
                    line=rng.get("line", 0) + 1,  # LSP is 0-indexed
                    col=rng.get("character", 0) + 1,
                    severity=severity_map.get(d.get("severity", 4), "info"),
                    message=d.get("message", ""),
                    code=str(d.get("code", "")),
                ))

            server.diagnostics[file_path] = diagnostics

    async def shutdown(self) -> None:
        """Shut down all running LSP servers."""
        for lang, server in self._servers.items():
            try:
                if server._reader_task:
                    server._reader_task.cancel()
                if server.process.returncode is None:
                    server.process.terminate()
                    await asyncio.wait_for(server.process.wait(), timeout=3)
            except Exception:
                try:
                    server.process.kill()
                except Exception:
                    pass
        self._servers.clear()


# --- Tool definition ---

DIAGNOSTICS_TOOL = {
    "name": "diagnostics",
    "description": (
        "Get LSP diagnostics (type errors, lint issues) for a file. "
        "Use after editing code to check for errors, or to investigate "
        "existing issues in a file."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to check"},
        },
        "required": ["path"],
    },
}


def make_diagnostics_handler(manager: LSPManager) -> Any:
    """Create a diagnostics tool handler."""

    async def handler(input_data: dict[str, Any]) -> str:
        file_path = input_data["path"]
        diagnostics = await manager.get_diagnostics(file_path)

        if not diagnostics:
            return f"No diagnostics for {file_path}"

        errors = [d for d in diagnostics if d.severity == "error"]
        warnings = [d for d in diagnostics if d.severity == "warning"]

        lines = [f"Diagnostics for {file_path}:"]
        if errors:
            lines.append(f"\n  Errors ({len(errors)}):")
            for d in errors[:15]:
                lines.append(f"    L{d.line}:{d.col} [{d.code}] {d.message}")
        if warnings:
            lines.append(f"\n  Warnings ({len(warnings)}):")
            for d in warnings[:10]:
                lines.append(f"    L{d.line}:{d.col} [{d.code}] {d.message}")

        return "\n".join(lines)

    return handler
