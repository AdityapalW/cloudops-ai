"""Best-effort reduction of common secrets in model-visible log data."""

import re

_SECRET_KEY = re.compile(
    r"password|passwd|secret|token|authorization|cookie|api[_-]?key|private[_-]?key", re.I
)
_PATTERNS = [
    (
        re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S),
        "[REDACTED PRIVATE KEY]",
    ),
    (re.compile(r"\bBearer\s+[^\s\"',;]+", re.I), "Bearer [REDACTED]"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{20,}"), "[REDACTED API KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[REDACTED JWT]"),
    (
        re.compile(r"((?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*)([^\s,;&]+)", re.I),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(https?://)[^/@\s:]+:[^/@\s]+@", re.I), r"\1[REDACTED]@"),
]


def redact(value, depth=0):
    if depth > 8:
        return "[OMITTED: nesting limit]"
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if _SECRET_KEY.search(str(k)) else redact(v, depth + 1)
            for k, v in list(value.items())[:100]
        }
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        for pattern, replacement in _PATTERNS:
            value = pattern.sub(replacement, value)
        return value[:2000] + (" [TRUNCATED]" if len(value) > 2000 else "")
    return value
