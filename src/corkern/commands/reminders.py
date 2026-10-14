"""remind / done / todos — v1-compatible reminders in memory/<slug>/todo.md.

File format (shared with v1, so migrated todo.md files keep working):
    ## Section header            (optional; open items are numbered across sections)
    - [ ] 2026-10-01 text        open
    - [x] 2026-10-01 text        done
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import typer

from corkern import guard
from corkern.config import LINE_BREAKS, MemoryRepo, conf_entries, load_config, valid_slug

OPEN_ITEM = re.compile(r"^- \[ \] ")
ITEM_PREFIX = re.compile(r"^- \[ \] (\d{4}-\d{2}-\d{2} )?")
GLOBAL = "global"


def fail(message: str) -> None:
    typer.echo(f"  ✗ {message}", err=True)          # v1 err()
    raise typer.Exit(1)


def header(title: str) -> None:
    typer.echo(f"\n{title}")                       # v1 hdr()


def slug_for_cwd(projects: dict[str, Path]) -> str | None:
    """The project whose path is exactly the current directory (v1 find_slug_for_path)."""
    cwd = Path.cwd().resolve()
    for slug, path in projects.items():
        if path.resolve() == cwd:
            return slug
    return None


def resolve_slug(given: str | None, projects: dict[str, Path]) -> str:
    slug = given or slug_for_cwd(projects) or GLOBAL
    if not valid_slug(slug):
        fail(f"Invalid slug {slug!r}.")
    return slug


def todo_path(repo: MemoryRepo, slug: str) -> Path:
    return repo.memory_dir / slug / "todo.md"


def read_todo(repo: MemoryRepo, slug: str) -> list[str] | None:
    path = todo_path(repo, slug)
    text = guard.read_shared(path)                # a symlinked todo.md counts as absent
    return text.split("\n") if text is not None else None


def open_items(lines: list[str]) -> list[tuple[str | None, str]]:
    """[(section or None, display text)] for every open item, in file order."""
    items, section = [], None
    for line in lines:
        if line.startswith("## "):
            section = line[3:]
        elif OPEN_ITEM.match(line):
            items.append((section, ITEM_PREFIX.sub("", line)))
    return items


def format_items(lines: list[str]) -> list[str]:
    out, n = [], 0
    for line in lines:
        if line.startswith("## "):
            out.append(f"  ── {line[3:]} ──")
        elif OPEN_ITEM.match(line):
            n += 1
            out.append(f"  {n:2d}. {ITEM_PREFIX.sub('', line)}")
    return out


def remind(
    text: str = typer.Argument(..., help="Reminder text"),
    slug: str = typer.Argument(None, help="Project slug (default: current project, else global)"),
) -> None:
    """Add a reminder (shown at the start of every Claude session)."""
    if not text.strip() or any(c in text for c in LINE_BREAKS):
        fail("Reminder text must be a single, non-empty line.")
    repo, projects = load_config()
    slug = resolve_slug(slug, projects)
    guard.append_text(todo_path(repo, slug), f"- [ ] {date.today():%Y-%m-%d} {text}\n")
    typer.echo(f"  ✓ Reminder added [{slug}]: {text}")


def done(
    n: int = typer.Argument(..., help="Number of the open reminder (from `todos`)"),
    slug: str = typer.Argument(None, help="Project slug (default: current project, else global)"),
) -> None:
    """Mark reminder N as done."""
    repo, projects = load_config()
    slug = resolve_slug(slug, projects)
    lines = read_todo(repo, slug)
    if lines is None:
        fail(f"No todo file for '{slug}'")
    count = 0
    for i, line in enumerate(lines):
        if OPEN_ITEM.match(line):
            count += 1
            if count == n:
                lines[i] = "- [x] " + line[len("- [ ] "):]
                guard.write_text(todo_path(repo, slug), "\n".join(lines))
                typer.echo(f"  ✓ Done: item {n} in [{slug}]")
                return
    fail(f"Item {n} not found — [{slug}] has {count} open reminder(s)")


def todos(
    target: str = typer.Argument(None, metavar="[SLUG]", help="A project slug"),
    all_: bool = typer.Option(False, "--all", help="Every tracked project + global"),
) -> None:
    """List open reminders (current project + global, a slug, or --all)."""
    repo, projects = load_config()
    if all_:
        seen: dict[str, None] = {}
        for s, _ in conf_entries(guard.read_text(repo.conf_file)):
            if valid_slug(s):
                seen[s] = None
        slugs = [*seen, GLOBAL]
    elif target:
        slugs = [resolve_slug(target, projects)]
    else:
        current = slug_for_cwd(projects)
        slugs = [s for s in (current, GLOBAL) if s]
        slugs = list(dict.fromkeys(slugs))
    shown = False
    for slug in slugs:
        lines = read_todo(repo, slug)
        if not lines or not open_items(lines):
            continue
        shown = True
        header(f"Open reminders: {slug}")
        for row in format_items(lines):
            typer.echo(row)
        typer.echo("")
    if not shown:
        typer.echo("  ✓ No open reminders")
