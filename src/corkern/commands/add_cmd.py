import sys
from pathlib import Path
import typer
from corkern import guard
from corkern.commands.signing_cmd import _ask
from corkern.config import (
    UnstorablePath, _parse_conf, check_projects, confirm_projects, conf_entries, load_config,
    resolve_project_path, same_path, store_project_path, valid_slug,
)


def _fail(message: str) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(1)


def add_project(
    slug: str = typer.Argument(None, help="Project slug (default: the folder name, like v1)"),
    path: Path = typer.Argument(None, help="Project path (default: cwd)"),
    confirm_all: bool = typer.Option(False, "--confirm-all",
                                     help="List every project not confirmed on this machine; confirm them after one question"),
    confirm_listed: bool = typer.Option(False, "--confirm-listed",
                                        help="Confirm the projects whose path matches the slug:path lines on stdin "
                                             "(this machine's own v1 registry; used by migrate/3)"),
) -> None:
    """Add a project to tracking — or confirm on this machine a project that is already registered.

    corkern.conf is shared: a project's path is used to read its Claude Code conversations
    (harvest, prune) or to wire its folder (init-device) only once this machine confirmed it.
    """
    if confirm_all or confirm_listed:
        _confirm_many(confirm_listed)
        return
    if slug is None:
        slug = (path or Path(".")).resolve().name
    if not valid_slug(slug):
        _fail(f"Invalid slug {slug!r}: use letters, digits, '.', '_' or '-', "
              "starting with a letter or digit.")
    resolved = (path or Path(".")).resolve()
    try:
        stored = store_project_path(resolved)
    except UnstorablePath as e:
        _fail(f"Invalid path {str(resolved)!r}: {e}.")

    repo, projects = load_config()
    # Like v1: any line for the slug counts, including other devices' (e.g. Windows) lines
    if slug in {s for s, _ in conf_entries(guard.read_text(repo.conf_file))}:
        check = check_projects(repo.path).get(slug)
        match = next((p for p in (check.candidates if check else ()) if same_path(p, resolved)), None)
        if match is None:
            _fail(f"Slug '{slug}' already exists. Remove it first.")
        _confirm(repo.path, {slug: match})
        typer.echo(f"Confirmed {slug} → {match} on this machine")
        return
    for other, other_path in projects.items():
        if other_path.resolve() == resolved:
            _fail(f"Path '{resolved}' is already tracked under slug '{other}'.")

    _confirm(repo.path, {slug: resolve_project_path(stored)})
    guard.append_text(repo.conf_file, f"{slug}:{stored}\n")
    typer.echo(f"Added {slug} → {resolved}")


def _confirm(repo: Path, items: dict[str, Path]) -> None:
    try:
        confirm_projects(repo, items)
    except (OSError, guard.V1ProtectedError) as e:
        _fail(f"Could not record the confirmation in {repo}/.git/corkern: {e}")


def _confirm_many(listed: bool) -> None:
    repo, _ = load_config()
    open_ = [c for c in check_projects(repo.path).values() if not c.confirmed]
    if listed:
        theirs = _parse_conf(sys.stdin.read(), "stdin")
        found: dict[str, Path] = {}
        for c in open_:
            if c.slug not in theirs:
                continue
            match = next((p for p in c.candidates if same_path(p, theirs[c.slug])), None)
            if match is None:
                typer.echo(f"  ! {c.slug}: the registry's path differs from the listed one "
                           f"({', '.join(map(str, c.candidates))} ≠ {theirs[c.slug]}) — not confirmed")
            else:
                found[c.slug] = match
        _confirm(repo.path, found)
        for slug, p in sorted(found.items()):
            typer.echo(f"  ✓ {slug} → {p} confirmed on this machine")
        return
    single = [c for c in open_ if not c.duplicate]
    if not open_:
        typer.echo("✓ Every project is confirmed on this machine.")
        return
    typer.echo("Not confirmed on this machine (corkern.conf is shared — check each path is yours):")
    for c in single:
        typer.echo(f"  {c.slug} → {c.candidates[0]}    ({c.lines[0]})")
    for c in open_:
        if c.duplicate:
            typer.echo(f"  ! {c.explain()}")
    if single and _ask(f"Confirm these {len(single)} project(s) on this machine? [y/N] ", False):
        _confirm(repo.path, {c.slug: c.candidates[0] for c in single})
        typer.echo(f"✓ {len(single)} project(s) confirmed")
    elif single:
        typer.echo("Nothing confirmed.")
