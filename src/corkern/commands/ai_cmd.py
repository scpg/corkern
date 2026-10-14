"""get-AIinstructions and session-context — what Claude sees at the start of a session.

`session-context` is called by the SessionStart hook; its stdout is injected into Claude's
context, so it prints only useful text and never fails loudly.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import typer

from corkern import devices, guard
from corkern.commands import metrics_cmd
from corkern.commands.reminders import GLOBAL, format_items, open_items, read_todo
from corkern.commands.secrets_cmd import log_entries
from corkern.commands.trust_cmd import UNTRUSTED, held_entries
from corkern.config import conf_entries, load_config, valid_slug
from corkern.knowledge_repo import repo_symlinks
from corkern.memory_index import usage

PROG = "corkern"


def instructions_text() -> str:
    repo, _ = load_config()
    rows = []
    for slug, raw in conf_entries(repo.read_conf()):
        if valid_slug(slug):
            rows.append(f"- `{slug}` → {raw.strip() or '~ (home)'}")
    projects = "\n".join(rows) or "- (none yet)"
    kr = repo.path
    return f"""# Corkern — AI Instructions

Corkern is this developer's memory sync system for Claude Code. A private git
"knowledge repo" keeps Claude's memory files in sync across machines automatically.

## What it does

1. Pulls other devices' memories when a session starts, and between replies
2. Commits each memory write immediately, with the file name in the commit message
3. Pushes memories (global ones right away, project ones at session end)
4. Never blocks Claude and never loses text: conflicting edits are kept side by side
   as `<name>.conflict-<host>-<time>.md` — mention these to the user if you see them

## Knowledge repo

  `{kr}`

Memory files live under `{kr}/memory/<slug>/`. Global (cross-project) memories live
under `{kr}/memory/global/`.

## Tracked projects

{projects}

## CLI ({PROG})

  {PROG} list | ls                    Tracked projects and whether their paths exist
  {PROG} add <slug> [path]            Track a project (default path: current directory)
  {PROG} remove | rm <slug>           Stop tracking a project
  {PROG} status | doctor              Knowledge repo and project overview
  {PROG} remind "text" [slug]         Add a reminder (default: current project, else global)
  {PROG} todos | todo [slug|--all]    List open reminders
  {PROG} done <n> [slug]              Mark reminder n as done
  {PROG} sessions [slug]              Recent session notes
  {PROG} log [slug]                   Git history of a project's memory
  {PROG} signing                      This machine's commit-signing key
  {PROG} trust                        Which devices' memories this machine accepts
  {PROG} secrets [--scan]             Secrets removed before commit / check the repo
  {PROG} init-device                  Point Claude Code's memory for each project here
  {PROG} iwe [--setup|--install]      IWE knowledge graph over the memories (MCP server)
  {PROG} graphify [slug] [--install] [--full]  Build/update the project knowledge graph
  {PROG} crg [slug] [--install]                code-review-graph (fast, AST-only)
  {PROG} metrics                             Memory size, context pressure, growth
  {PROG} harvest [slug] [--all] [-i]         Memories from past Claude conversations
  {PROG} refine [slug]                       Split harvested-*.md bulk files
  {PROG} prune [slug]                        Conversation inventory (harvested / pending)
  {PROG} distill [--auto]                    Cross-project synthesis with Claude
                                               (confirm with the user first)
  {PROG} memory-prune [slug] [--dry-run]       Archive expired/stale memories (never deletes;
                                               confirm with the user first)
  {PROG} get-AIinstructions | ai      This document

## Secrets

Never write passwords, tokens or keys into memory files. If one slips in, corkern replaces it
with `[secret removed by corkern: <kind>]` before the commit; the original stays on this
machine only. Tell the user when you see such a marker.

## Reminders

Add a reminder when work starts but won't finish this session, or when the user wants
to come back to something; clear it with `{PROG} done <n> [slug]` when the work is done.
Open reminders are shown at the start of every session.

## Session notes

At the end of significant work, write a short note (5–10 bullets: done, next, decisions):

  `{kr}/memory/<slug>/sessions/YYYY-MM-DD-HHmm.md`

It is committed and synced automatically; the next session on any machine sees it.
"""


def get_ai_instructions() -> None:
    """Print the corkern context document for AI assistants."""
    typer.echo(instructions_text())


def _box(title: str, rows: list[str], footer: str | None = None) -> str:
    lines = [f"╔═ Corkern: {title} " + "═" * max(3, 50 - len(title))]
    lines += [f"║{row}" for row in rows]
    if footer:
        lines.append(f"║  {footer}")
    lines.append("╚" + "═" * 55)
    return "\n".join(lines)


def _todo_box(repo, slug: str) -> str | None:
    lines = read_todo(repo, slug)
    if not lines or not open_items(lines):
        return None
    return _box(f"open reminders [{slug}]", format_items(lines),
                f"→ clear with: {PROG} done <n> {slug}")


def _surfacing_files(repo, slug: str) -> str | None:
    """Memory files marked `surfaces: true` in their frontmatter (v1 'actionable' files)."""
    rows = []
    folder = repo.memory_dir / slug
    for name, _ in sorted(guard.list_files(folder, ".md")):
        if name in ("MEMORY.md", "todo.md"):
            continue
        head = guard.read_text(folder / name).split("\n")[:10]
        if any("".join(line.split()) == "surfaces:true" for line in head):
            desc = next((line.split(":", 1)[1].strip().strip('"') for line in head
                         if line.startswith("description:")), "")
            rows.append(f"  → {name}" + (f" — {desc}" if desc else ""))
    return _box(f"actionable memory files [{slug}]", rows) if rows else None


def _safety_boxes(repo, slug: str | None) -> list[str]:
    """Things the user must know at session start: held-back commits, removed secrets,
    an index Claude Code no longer loads completely."""
    boxes = []
    links = repo_symlinks(repo.path)
    if links:
        boxes.append(_box("symlinks in the knowledge repo — held/ignored", [
            "  These synced files are symlinks; corkern never follows them and treats them",
            "  as absent (a link can point at any file on this machine):",
            *[f"    {rel}" for rel in links[:10]],
            *([f"    … and {len(links) - 10} more"] if len(links) > 10 else []),
            "  Tell the user. Do not read or write them; replace them with regular files."],
            f"→ {PROG} status lists them"))
    entries = held_entries(repo.path)
    if entries:
        untrusted = len({sha for sha, reason in entries if reason == UNTRUSTED})
        reasons = list(dict.fromkeys(reason for sha, reason in entries if reason != UNTRUSTED))
        boxes.append(_box("memories held back", [
            "  Nothing new from the remote was loaded this session. Reasons:",
            *([f"  - {untrusted} commit(s) not signed by a device this machine trusts"] if untrusted else []),
            *[f"  - {reason}" for reason in reasons[:8]],
            "  Tell the user; never recreate held content by hand. Review with:"],
            f"→ {PROG} trust"))
    recent = [e for e in log_entries()
              if e[0] >= f"{datetime.now() - timedelta(days=3):%Y-%m-%d %H:%M:%S}"]
    if recent:
        rows = [f"  {when[:16]}  {rel}  ({kinds})" for when, rel, kinds, _ in recent[-5:]]
        boxes.append(_box("secrets removed before commit", [
            "  A secret was written into a memory and replaced by a marker before syncing:",
            *rows, "  Tell the user, and do not write secrets into memory files."],
            f"→ originals stay on this machine: {PROG} secrets"))
    index = guard.read_shared(repo.memory_dir / slug / "MEMORY.md") if slug else None
    if index is not None:
        u = usage(index)
        if u.over or u.near:
            state = "OVER" if u.over else "close to"
            boxes.append(_box(f"MEMORY.md {state} Claude Code's read limit [{slug}]", [
                f"  {u.describe()}" + (" — entries past it are not loaded." if u.over else "."),
                "  Please shorten it now: one line per entry, details in topic files,",
                "  merge or drop stale entries (lines also arrive from other devices)."]))
    return boxes


def _cwd_from_hook_stdin() -> Path:
    if not sys.stdin.isatty():
        try:
            event = json.loads(sys.stdin.read() or "{}")
            if isinstance(event, dict) and event.get("cwd"):
                return Path(event["cwd"])
        except (json.JSONDecodeError, OSError):
            pass
    return Path.cwd()


def session_context() -> None:
    """(For the SessionStart hook) instructions, open reminders, actionable files."""
    repo, projects = load_config()
    cwd = _cwd_from_hook_stdin().resolve()
    slug = next((s for s, p in projects.items() if p.resolve() == cwd), None)
    text = instructions_text()
    try:
        devices.update(repo, projects, touch=slug)  # this device's manifest: last seen here
    except (OSError, guard.SymlinkInRepoError):
        pass                                       # never break session start
    if slug:
        try:
            metrics_cmd.record(repo, slug, text)
        except (OSError, guard.SymlinkInRepoError):
            pass                                   # metrics must never break session start
    parts = [text, *_safety_boxes(repo, slug)]
    for s in [GLOBAL] + ([slug] if slug and slug != GLOBAL else []):
        parts += [box for box in (_todo_box(repo, s), _surfacing_files(repo, s)) if box]
    typer.echo("\n".join(parts))
