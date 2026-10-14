"""sessions / log — v1-compatible views of a project's memory history."""
from __future__ import annotations

import typer

from corkern import guard
from corkern.commands.reminders import fail, header, slug_for_cwd
from corkern.config import load_config, valid_slug

RECENT = 5


def sessions(slug: str = typer.Argument(None, help="Project slug (default: current project)")) -> None:
    """Show the most recent session notes for a project."""
    repo, projects = load_config()
    slug = slug or slug_for_cwd(projects)
    if not slug:
        fail("Could not detect project slug. Usage: corkern sessions <slug>\n"
             f"Known slugs: {' '.join(sorted(projects))}")
    if not valid_slug(slug):
        fail(f"Invalid slug {slug!r}.")
    header(f"Recent sessions: {slug}")
    folder = repo.memory_dir / slug / "sessions"
    # Newest first by NAME (notes are YYYY-MM-DD-HHmm.md). v1 used mtime, which on a freshly
    # cloned/pulled machine is the checkout time — so it showed arbitrary notes there.
    notes = sorted(guard.list_files(folder, ".md"), key=lambda f: f[0], reverse=True)[:RECENT]
    if not notes:
        typer.echo(f"  ! No session notes yet for '{slug}'")
        typer.echo("  Claude writes them automatically — see global/CLAUDE.md for the instruction.")
        typer.echo("")
        return
    for name, _ in notes:
        typer.echo(f"\n  {name[:-3]}\n  {'─' * 50}")
        for line in guard.read_text(folder / name).rstrip("\n").split("\n"):
            typer.echo(f"  {line}")
    typer.echo("")


def log(slug: str = typer.Argument(None, help="Project slug (default: all projects)")) -> None:
    """Git history of a project's memory (or of all memory)."""
    repo, _ = load_config()
    if slug and not valid_slug(slug):
        fail(f"Invalid slug {slug!r}.")
    header(f"Memory log: {slug or 'all projects'}")
    path = f"memory/{slug}/" if slug else "memory/"
    try:
        out = guard.run_git(repo.path, "log", "--oneline", "--", path).stdout.rstrip()
        if out:
            typer.echo(out)
    except guard.GitError:
        typer.echo(f"  ! No commits found for {path}")
    typer.echo("")
