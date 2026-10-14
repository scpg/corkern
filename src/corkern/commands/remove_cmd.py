import typer
from corkern import guard
from corkern.config import LOCAL_CONF_FILE, conf_entries, conf_lines, load_config


def remove_project(
    slug: str = typer.Argument(..., help="Project slug to remove"),
) -> None:
    """Remove a project from tracking (every device's line for it, like v1)."""
    repo, _ = load_config()
    text = guard.read_text(repo.conf_file)

    kept, removed = [], 0
    for line in conf_lines(text):
        entry = next(conf_entries(line), None)
        if entry and entry[0] == slug:
            removed += 1
        else:
            kept.append(line)

    if removed == 0:
        local = repo.path / LOCAL_CONF_FILE
        if local.is_file() and slug in {s for s, _ in conf_entries(guard.read_text(local))}:
            typer.echo(f"Slug '{slug}' is only defined in {LOCAL_CONF_FILE} (a per-device "
                       "override). Edit that file to remove it.", err=True)
        else:
            typer.echo(f"Slug '{slug}' not found.", err=True)
        raise typer.Exit(1)

    guard.write_text(repo.conf_file, "\n".join(kept))
    plural = "line" if removed == 1 else "lines"
    typer.echo(f"Removed {slug} ({removed} {plural}, all devices)")
