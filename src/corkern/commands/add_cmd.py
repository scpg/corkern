from pathlib import Path
import typer
from corkern import guard
from corkern.config import (
    UnstorablePath, conf_entries, load_config, store_project_path, valid_slug,
)


def _fail(message: str) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(1)


def add_project(
    slug: str = typer.Argument(None, help="Project slug (default: the folder name, like v1)"),
    path: Path = typer.Argument(None, help="Project path (default: cwd)"),
) -> None:
    """Add a project to tracking."""
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
        _fail(f"Slug '{slug}' already exists. Remove it first.")
    for other, other_path in projects.items():
        if other_path.resolve() == resolved:
            _fail(f"Path '{resolved}' is already tracked under slug '{other}'.")

    guard.append_text(repo.conf_file, f"{slug}:{stored}\n")
    typer.echo(f"Added {slug} → {resolved}")
