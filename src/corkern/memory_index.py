"""Claude Code's read limit for MEMORY.md: only the first 200 lines or 25 KB are loaded.

Claude Code itself reminds Claude when Claude's own write pushes the index near the limit.
With corkern, lines also arrive from other devices (MEMORY.md merges line-wise), so an index
can cross the limit without Claude writing it — entries past the limit are silently not
loaded. These checks let `status` and the session context say so.
"""
from __future__ import annotations

from dataclasses import dataclass

LIMIT_LINES = 200
LIMIT_BYTES = 25_000          # "25KB" in the docs; the smaller reading, to warn early enough
NEAR = 0.9


@dataclass(frozen=True)
class IndexUsage:
    lines: int
    size: int

    @property
    def over(self) -> bool:
        return self.lines > LIMIT_LINES or self.size > LIMIT_BYTES

    @property
    def near(self) -> bool:
        return not self.over and (self.lines >= LIMIT_LINES * NEAR or self.size >= LIMIT_BYTES * NEAR)

    def describe(self) -> str:
        return f"{self.lines} lines / {self.size / 1000:.1f} KB (limit {LIMIT_LINES} lines / 25 KB)"


def usage(text: str) -> IndexUsage:
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return IndexUsage(lines, len(text.encode()))
