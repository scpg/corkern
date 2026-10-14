"""memory-prune — archive expired / stale memory files (v1 parity; never deletes).

A memory file is a candidate when its frontmatter has `expires: YYYY-MM-DD` on or before
today, or `status: stale`. Candidates move to memory/<slug>/archive/<YYYYMMDD>-<name>.
Differences from v1: an existing archive file is never overwritten (a counter is added),
and without a terminal the default answer is "no" unless --yes is given.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import typer

from corkern import guard
from corkern.commands.reminders import fail, header, slug_for_cwd
from corkern.config import load_config, valid_slug

EXPIRES = re.compile(r"^\s*expires:\s*(\d{4}-\d{2}-\d{2})")
STALE = re.compile(r"^\s*status:\s*stale")
SKIP_NAMES = {"MEMORY.md", "todo.md"}


def _frontmatter(text: str) -> list[str]:
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return []
    out = []
    for line in lines[1:]:
        if line == "---":
            break
        out.append(line)
    return out


def _candidates(mem_dir: Path) -> tuple[list[tuple[Path, str]], list[tuple[Path, str]]]:
    expired, stale = [], []
    today = date.today()
    for f in guard.walk_files(mem_dir, ".md"):
        rel = f.relative_to(mem_dir).parts
        if f.name in SKIP_NAMES or "sessions" in rel[:-1] or "archive" in rel[:-1]:
            continue
        front = _frontmatter(guard.read_text(f))
        exp = next((m.group(1) for line in front if (m := EXPIRES.match(line))), None)
        if exp:
            try:
                if date.fromisoformat(exp) <= today:
                    expired.append((f, f"expired on {exp}"))
                    continue
            except ValueError:
                pass
        if any(STALE.match(line) for line in front):
            stale.append((f, "marked status: stale"))
    return expired, stale


def _ask(question: str, yes: bool) -> bool:
    if yes:
        return True
    typer.echo(question, nl=False)
    answer = sys.stdin.readline().strip()
    return answer in ("y", "Y")


def memory_prune(
    slug: str = typer.Argument(None, help="Project slug (default: current project)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List candidates; move nothing"),
    yes: bool = typer.Option(False, "--yes", help="Archive without asking"),
) -> None:
    """Archive expired or stale memory files (never deletes)."""
    repo, projects = load_config()
    slug = slug or slug_for_cwd(projects)
    if not slug:
        fail("Could not detect project slug.\n  Usage: corkern memory-prune [slug] [--dry-run]\n"
             f"  Known slugs: {' '.join(sorted(projects))}")
    if not valid_slug(slug):
        fail(f"Invalid slug {slug!r}.")
    mem_dir = repo.memory_dir / slug
    if not mem_dir.is_dir():
        fail(f"No memory directory for '{slug}'")

    header(f"corkern memory-prune: {slug}")
    if dry_run:
        typer.echo("  (dry run — no files will be moved)")
    typer.echo("")
    expired, stale = _candidates(mem_dir)
    total = len(expired) + len(stale)
    if total == 0:
        typer.echo(f"  ✓ No expired or stale memory files in [{slug}].")
        typer.echo("  To mark a file stale, add  status: stale  inside its frontmatter.")
        typer.echo("")
        return
    for title, group in (("Expired (expires: date is past):", expired),
                         ("Stale (status: stale in frontmatter):", stale)):
        if group:
            typer.echo(f"  {title}")
            for f, why in group:
                typer.echo(f"  • {f.name:<40}  {why}")
            typer.echo("")
    typer.echo(f"  {total} candidate(s) — all others remain untouched.")
    typer.echo("")
    if dry_run:
        typer.echo("  Dry run done. Re-run without --dry-run to archive.")
        typer.echo("")
        return
    if not _ask(f"  Archive these {total} file(s) to memory/{slug}/archive/? [y/N] ", yes):
        typer.echo("  Aborted. Nothing moved.")
        typer.echo("")
        return

    stamp = date.today().strftime("%Y%m%d")
    archive = mem_dir / "archive"
    for f, _ in expired + stale:
        dest = archive / f"{stamp}-{f.name}"
        n = 1
        while dest.exists():                      # v1's mv would overwrite — never lose a file
            dest = archive / f"{stamp}-{n}-{f.name}"
            n += 1
        guard.move(f, dest)
        typer.echo(f"  ✓ Archived: {f.name} → archive/{dest.name}")
    typer.echo("")
    typer.echo(f"  ✓ {total} file(s) archived. Restore with: mv archive/<file> {mem_dir}/")
    # v1 says the PostToolUse hook commits it — that hook only fires on Claude's Write/Edit
    typer.echo("  (The next corkern hook run — end of a Claude reply or session start — commits it.)")
    typer.echo("")
