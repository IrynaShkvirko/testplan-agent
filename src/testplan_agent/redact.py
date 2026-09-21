"""Redaction of secrets and personal data before anything is bundled or sent to a model.

Line-preserving on purpose: a diff's hunk headers count lines, so a redacted diff must have
exactly as many lines as the original.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Counter as CounterType
from typing import List, Pattern, Tuple

_PRIVATE_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PRIVATE_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")

# (kind, pattern, group to replace; 0 means the whole match)
_RULES: List[Tuple[str, Pattern[str], int]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), 0),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), 0),
    ("bearer_token", re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{20,})"), 1),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), 0),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|passwd|password|pwd)\b\s*[:=]\s*['\"]?"
            r"([^\s'\"#,;]{8,})"
        ),
        1,
    ),
    (
        "email",
        re.compile(
            r"\b[A-Za-z0-9._%+-]+@(?!example\.(?:com|org|net)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
        ),
        0,
    ),
]


def redact(text: str) -> Tuple[str, CounterType[str]]:
    """Return the text with secrets replaced by ``[REDACTED:kind]`` and a count per kind."""
    counts: CounterType[str] = Counter()
    out: List[str] = []
    in_key = False
    for line in text.split("\n"):
        if in_key:
            out.append("[REDACTED:private_key]")
            if _PRIVATE_END.search(line):
                in_key = False
            continue
        if _PRIVATE_BEGIN.search(line):
            counts["private_key"] += 1
            out.append("[REDACTED:private_key]")
            in_key = not _PRIVATE_END.search(line)
            continue
        for kind, pattern, group in _RULES:

            def _sub(match: re.Match[str], kind: str = kind, group: int = group) -> str:
                counts[kind] += 1
                token = f"[REDACTED:{kind}]"
                if group == 0:
                    return token
                start, end = match.span(group)
                whole = match.group(0)
                offset = match.start()
                return whole[: start - offset] + token + whole[end - offset :]

            line = pattern.sub(_sub, line)
        out.append(line)
    return "\n".join(out), counts
