"""Security guardrail agent.

Pure rule-based, no LLM dependency. Runs pre-execution only.
Blocks injection attempts, path traversal, command injection,
and secret exfiltration. Always blocking -- failure stops the pipeline.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import urllib.parse

from opentf.agents.base import AgentResult, AgentRole, BaseAgent, GuardrailPhase
from opentf.models.context import AgentContext

log = logging.getLogger(__name__)

# Prompt injection patterns
INJECTION_PATTERNS = [
    "ignore previous",
    "ignore all previous",
    "ignore all instructions",
    "disregard all",
    "disregard your",
    "you are now",
    "new instructions:",
    "system:",
    "forget everything",
    "forget your instructions",
    "override your",
    "pretend you are",
    "act as if you",
    "do not follow",
    "bypass your",
    "jailbreak",
    "ignore safety",
    "ignore guardrails",
    "developer mode",
    "dan mode",
    "from now on",
    "your new role",
    "respond only in",
]

# Path traversal patterns
PATH_TRAVERSAL_PATTERNS = [
    re.compile(r"\.\./\.\./"),  # repeated ../
    re.compile(r"/etc/(passwd|shadow|hosts)"),
    re.compile(r"/proc/self/"),
    re.compile(r"~root/"),
    re.compile(r"C:\\Windows\\System32", re.IGNORECASE),
    re.compile(r"\.\.%2[fF]"),         # URL-encoded ../
    re.compile(r"%2[eE]%2[eE]"),       # URL-encoded ..
]

# Command injection patterns (dangerous when fed to shells)
COMMAND_INJECTION_PATTERNS = [
    re.compile(r";\s*rm\s"),
    re.compile(r"&&\s*rm\s"),
    re.compile(r"\|\s*rm\s"),
    re.compile(r"`[^`]*`"),  # backtick execution
    re.compile(r"\$\([^)]*\)"),  # $(command) execution
    re.compile(r";\s*sudo\s"),
    re.compile(r";\s*chmod\s"),
    re.compile(r";\s*dd\s+if="),
    re.compile(r"mkfs\.\w+"),
    re.compile(r":(){ :\|:& };:"),  # fork bomb
    re.compile(r"\|\s*bash"),          # pipe to bash
    re.compile(r"\|\s*sh\s"),          # pipe to sh
    re.compile(r"\|\s*curl\s"),        # pipe to curl
    re.compile(r"\|\s*wget\s"),        # pipe to wget
    re.compile(r"bash\s+-c\s+"),       # bash -c execution
    re.compile(r"sh\s+-c\s+"),         # sh -c execution
]

# Exfiltration patterns (requesting internal state)
EXFILTRATION_PATTERNS = [
    "output your system prompt",
    "show me your instructions",
    "what are your rules",
    "reveal your prompt",
    "print your system message",
    "tell me your api key",
    "show me the api key",
    "output the credentials",
    "what is the secret key",
    "dump your config",
    "export your memory",
]


class SecurityAgent(BaseAgent):
    """Rule-based input safety gate. No LLM. Always blocking."""

    def __init__(self) -> None:
        super().__init__(
            name="security",
            description="Input safety gate -- blocks injection, exfiltration, and unsafe patterns",
            capabilities=["security"],
            role=AgentRole.GUARDRAIL,
            phases=[GuardrailPhase.PRE],
        )

    async def process(self, context: AgentContext) -> AgentResult:
        text = context.task.description
        # Strip zero-width and invisible characters, then normalize Unicode
        _invisible = str.maketrans("", "", "\u200b\u200c\u200d\u2060\ufeff")
        text_normalized = unicodedata.normalize("NFKC", text.translate(_invisible))
        # Decode URL encoding (defeat %2f bypasses)
        text_lower = urllib.parse.unquote(text_normalized).lower()

        # Empty input
        if len(text.strip()) < 1:
            return AgentResult(success=False, errors=["Empty input."])

        # Excessive length
        if len(text) > 100_000:
            return AgentResult(success=False, errors=["Input exceeds maximum length."])

        # Injection patterns
        for pattern in INJECTION_PATTERNS:
            if pattern in text_lower:
                log.warning("Injection pattern detected: %r", pattern)
                return AgentResult(
                    success=False,
                    errors=["Blocked: potential prompt injection detected."],
                )

        # Path traversal (check both raw and normalized)
        for regex in PATH_TRAVERSAL_PATTERNS:
            if regex.search(text_lower):
                log.warning("Path traversal pattern detected")
                return AgentResult(
                    success=False,
                    errors=["Blocked: potential path traversal detected."],
                )

        # Command injection (check normalized text)
        for regex in COMMAND_INJECTION_PATTERNS:
            if regex.search(text_lower):
                log.warning("Command injection pattern detected")
                return AgentResult(
                    success=False,
                    errors=["Blocked: potential command injection detected."],
                )

        # Exfiltration
        for pattern in EXFILTRATION_PATTERNS:
            if pattern in text_lower:
                log.warning("Exfiltration pattern detected: %r", pattern)
                return AgentResult(
                    success=False,
                    errors=["Blocked: potential exfiltration attempt detected."],
                )

        return AgentResult(success=True, output={"security_cleared": True})


# --- File content security scanning (reusable by TaskForce) ---

# Patterns for scanning generated/modified code
_CODE_SECURITY_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # Hardcoded secrets
    (re.compile(r"""(?:api[_-]?key|secret|password|token|auth)\s*[=:]\s*["'][^"']{8,}["']""", re.IGNORECASE),
     "hardcoded_secret", "Possible hardcoded secret or API key"),
    (re.compile(r"""sk-[a-zA-Z0-9]{20,}"""), "api_key_exposed", "Exposed API key pattern (sk-...)"),
    (re.compile(r"""ghp_[a-zA-Z0-9]{36}"""), "github_token", "GitHub personal access token"),
    (re.compile(r"""-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"""), "private_key", "Private key in source"),

    # Dangerous code patterns
    (re.compile(r"""\beval\s*\("""), "eval_usage", "Use of eval() -- potential code injection"),
    (re.compile(r"""\bexec\s*\("""), "exec_usage", "Use of exec() -- potential code injection"),
    (re.compile(r"""__import__\s*\("""), "dynamic_import", "Dynamic import -- potential code injection"),
    (re.compile(r"""\bos\.system\s*\("""), "os_system", "os.system() -- use subprocess instead"),
    (re.compile(r"""subprocess\..*shell\s*=\s*True"""), "shell_true", "subprocess with shell=True -- command injection risk"),

    # SQL injection
    (re.compile(r"""f["'].*(?:SELECT|INSERT|UPDATE|DELETE|DROP).*\{""", re.IGNORECASE),
     "sql_injection", "SQL query built with f-string -- use parameterized queries"),
    (re.compile(r"""["'].*(?:SELECT|INSERT|UPDATE|DELETE|DROP).*["']\s*%""", re.IGNORECASE),
     "sql_injection_pct", "SQL query with % formatting -- use parameterized queries"),

    # XSS
    (re.compile(r"""innerHTML\s*="""), "xss_innerhtml", "innerHTML assignment -- XSS risk"),
    (re.compile(r"""document\.write\s*\("""), "xss_docwrite", "document.write() -- XSS risk"),
    (re.compile(r"""\bMarkup\s*\(.*\{"""), "xss_markup", "Unescaped markup interpolation"),

    # Path traversal
    (re.compile(r"""open\s*\(.*\+.*\)"""), "path_concat", "File open with string concatenation -- path traversal risk"),

    # Insecure
    (re.compile(r"""verify\s*=\s*False"""), "ssl_verify_false", "SSL verification disabled"),
    (re.compile(r"""chmod\s+777"""), "chmod_777", "chmod 777 -- world-writable permissions"),
    (re.compile(r"""0\.0\.0\.0"""), "bind_all", "Binding to 0.0.0.0 -- exposed to all interfaces"),
]


def scan_file_content(content: str, file_path: str) -> list[str]:
    """Scan file content for security issues. Returns list of finding strings.

    Used by TaskForce security review and can be used standalone.
    """
    findings: list[str] = []
    lines = content.splitlines()

    for pattern, code, description in _CODE_SECURITY_PATTERNS:
        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                findings.append(f"  [{code}] {file_path}:{i} -- {description}")
                break  # One finding per pattern per file

    return findings
