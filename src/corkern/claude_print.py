"""Text-in/text-out `claude -p` calls (distill --auto, harvest, refine).

The prompt carries memory text that teammates wrote, so these calls get nothing beyond the
text: no tools at all (`--tools ""`), only the user's own settings (`--setting-sources user`:
no project or local settings, so no hooks from a repo), no MCP servers (`--strict-mcp-config`
without `--mcp-config`), and they start in a fresh private empty folder instead of the
knowledge repo (guard.capture_tool_isolated). Flags checked against Claude Code 2.1.285 and
2.1.287 (`claude --help`).
"""
from __future__ import annotations

from pathlib import Path

from corkern import guard

ISOLATION = ("--setting-sources", "user", "--strict-mcp-config", "--tools", "")


def ask(claude: str, prompt: str, model: str | None = None) -> "guard.subprocess.CompletedProcess":
    argv = [claude, "-p", *ISOLATION, *(["--model", model] if model else [])]
    return guard.capture_tool_isolated(argv, prompt)


def interactive_cwd(preferred: Path | None = None) -> Path:
    """Where an interactive claude session starts: `preferred` (default: the current folder)
    unless it is missing or inside the knowledge repo — then $HOME."""
    where = Path(preferred) if preferred is not None else Path.cwd()
    if not where.is_dir() or guard.in_knowledge_repo(where):
        return Path.home()
    return where
