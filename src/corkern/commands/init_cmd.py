import os
from pathlib import Path
import typer
from corkern import guard
from corkern.knowledge_repo import InitError, init_repo


def init(
    path: Path = typer.Argument(None, help="Where to create it (default: $CORKERN_MEMORY_DIR)"),
) -> None:
    """Create a new knowledge repo (structure, .gitignore, initial commit)."""
    if path is None:
        env = os.environ.get("CORKERN_MEMORY_DIR")
        if not env:
            typer.echo("Give a path or set CORKERN_MEMORY_DIR.")
            raise typer.Exit(1)
        path = Path(env)

    try:
        created = init_repo(path)
    except (InitError, guard.GitError) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    target = guard.assert_safe(path)
    if not created:
        typer.echo(f"{target} is already a corkern knowledge repo — nothing to do.")
        return
    typer.echo(f"Initialized corkern knowledge repo at {target}")
    typer.echo(f"Next: export CORKERN_MEMORY_DIR={target}")
