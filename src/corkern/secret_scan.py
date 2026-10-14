"""Find and redact secrets in memory text before it is committed and pushed.

Claude writes memories by itself and the hooks push them automatically, so a token Claude
noted down would otherwise reach the remote (and its history). Two kinds of rules:

- known token formats (GitHub, Anthropic, OpenAI, AWS, Slack, Google API + OAuth, GitLab, npm, private
  key blocks) — redacted wherever they appear
- `password = …`, `api_key: …` and similar assignments — the value is redacted when it looks
  like a credential (12+ characters, letters and digits, not a path or placeholder)

Pure functions, no file access: the `secrets` command and the hooks do the I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MARKER = "[secret removed by corkern: {kind}]"

KNOWN: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S)),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("google-oauth-secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{28}")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}")),
]

ASSIGNMENT = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9])               # DB_PASSWORD and MY_API_KEY count, mypassword does not
    (?:password|passwd|pwd|secret|client[_-]?secret|token|access[_-]?token|auth[_-]?token
       |api[_-]?key|access[_-]?key|secret[_-]?key|private[_-]?key)
    ["'`]?[ \t]*[:=][ \t]*["'`]?
    (?P<value>[^\s"'`]{12,})""")

# Pre-filter for the bash hooks (grep -iE): every rule above contains one of these, so a file
# without any match cannot contain a secret and Python is not started. Kept in sync by tests.
HINT = (r"PRIVATE KEY|gh[pousr]_|github_pat_|sk-|AKIA|ASIA|xox[abprs]-|AIza|GOCSPX-|glpat-|npm_"
        r"|passw|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key")


@dataclass(frozen=True)
class Hit:
    kind: str
    start: int
    end: int

    def line(self, text: str) -> int:
        return text.count("\n", 0, self.start) + 1


def _credential_like(value: str) -> bool:
    if value.startswith(("/", "~", "$", "<", "{", "%", ".", "[")) or "${" in value:
        return False                                   # paths, variables, placeholders
    if not (re.search(r"[A-Za-z]", value) and re.search(r"\d", value)):
        return False                                   # NAMES_LIKE_THIS, plain words, numbers
    return not re.fullmatch(r"(?i)(x+|\*+|redacted|changeme|example\w*|your[_-]\w+)", value)


def scan(text: str) -> list[Hit]:
    """Non-overlapping secret spans, in text order."""
    hits = [Hit(kind, m.start(), m.end()) for kind, rx in KNOWN for m in rx.finditer(text)]
    hits += [Hit("assigned-credential", m.start("value"), m.end("value"))
             for m in ASSIGNMENT.finditer(text) if _credential_like(m.group("value"))]
    kept: list[Hit] = []
    for hit in sorted(hits, key=lambda h: (h.start, -(h.end - h.start))):
        if not kept or hit.start >= kept[-1].end:
            kept.append(hit)
    return kept


def redact(text: str) -> tuple[str, list[Hit]]:
    """Text with every secret replaced by a marker, and the hits that were replaced."""
    hits = scan(text)
    out, pos = [], 0
    for hit in hits:
        out += [text[pos:hit.start], MARKER.format(kind=hit.kind)]
        pos = hit.end
    out.append(text[pos:])
    return "".join(out), hits
