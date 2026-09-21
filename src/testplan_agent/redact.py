"""Redaction of secrets and personal data before anything is bundled or sent to a model.

Line-preserving on purpose: a diff's hunk headers count lines, so a redacted diff must have
exactly as many lines as the original.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Counter as CounterType
from typing import List, Pattern, Tuple, Union

_PRIVATE_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PRIVATE_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")

# A value assigned to a secret-looking name is left alone when it reads as code, not as a secret:
# another name (new_password, settings.api_key, DEFAULT_TOKEN). Values already redacted are kept.
_CODE_VALUE = re.compile(
    r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+|[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+"
)

# (kind, pattern, group(s) to replace; 0 means the whole match, a tuple names alternatives)
_RULES: List[Tuple[str, Pattern[str], Union[int, Tuple[str, ...]]]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), 0),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), 0),
    ("bearer_token", re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{20,})"), 1),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), 0),
    (
        "credential_assignment",
        # The secret word may sit inside a longer name (DB_PASSWORD, stripeApiKey, x-api-key)
        # and the name may be a quoted JSON or YAML key. A bare value must end the token, so a
        # call or a type (request.headers.get(...), Optional[str]) is not mistaken for a secret.
        re.compile(
            r"(?i)\b[\w-]*?(?:api[_-]?key|secret|token|passwd|password|pwd)\w*['\"]?\s*[:=]\s*"
            r"(?:(?P<q>['\"])(?P<quoted>[^\s'\"]{8,})(?P=q)"
            r"|(?P<bare>[^\s'\"#,;()\[\]{}]{8,})(?=$|[\s#,;]))"
        ),
        ("quoted", "bare"),
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

            def _sub(
                match: re.Match[str],
                kind: str = kind,
                group: Union[int, Tuple[str, ...]] = group,
            ) -> str:
                token = f"[REDACTED:{kind}]"
                if group == 0:
                    counts[kind] += 1
                    return token
                if isinstance(group, tuple):
                    group = next(g for g in group if match.group(g) is not None)
                    value = match.group(group)
                    if value.startswith("[REDACTED:") or (
                        group == "bare" and _CODE_VALUE.fullmatch(value)
                    ):
                        return match.group(0)
                counts[kind] += 1
                start, end = match.span(group)
                whole = match.group(0)
                offset = match.start()
                return whole[: start - offset] + token + whole[end - offset :]

            line = pattern.sub(_sub, line)
        out.append(line)
    return "\n".join(out), counts
