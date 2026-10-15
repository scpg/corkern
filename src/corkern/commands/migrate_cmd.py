import os
from pathlib import Path
import typer
from corkern import guard
from corkern.knowledge_repo import InitError
from corkern.migrate import migrate as run_migrate


def migrate(
    bundle: Path = typer.Argument(..., help="A corkern export (corkern export / corkern export) or a v1 git-archive bundle"),
    dest: Path = typer.Argument(None, help="New knowledge repo (default: $CORKERN_MEMORY_DIR)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be migrated; write nothing."),
) -> None:
    """Build a new knowledge repo from an export. v1 itself is never read.

    Preferred: a corkern export (`corkern export` in v1) — verified, includes not yet committed
    data. Also accepted: a git-archive bundle (committed v1 data only):

      git -C ~/dev/corkern archive HEAD memory .devices global projects.conf > ~/v1-export.tar
    """
    if dest is None:
        env = os.environ.get("CORKERN_MEMORY_DIR")
        if not env:
            typer.echo("Give a destination or set CORKERN_MEMORY_DIR.")
            raise typer.Exit(1)
        dest = Path(env)

    try:
        plan = run_migrate(bundle, dest, dry_run=dry_run)
    except (guard.BundleError, InitError, guard.GitError) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    target = guard.assert_safe(dest)
    rows = [
        ("bundle", guard.assert_safe(bundle)),
        ("source", plan.source),
        ("destination", target),
        ("projects", plan.projects),
        ("memory files", plan.memory_files),
        ("devices", plan.devices),
        ("global file", "yes" if "global/CLAUDE.md" in plan.files else "no"),
        ("skipped", len(plan.skipped)),
    ]
    typer.echo("DRY RUN — nothing written" if dry_run else "Migrated v1 export bundle")
    for label, value in rows:
        typer.echo(f"  {label:<12} : {value}")
    for name in plan.skipped:
        typer.echo(f"      - {name}")
    for line in plan.report():
        typer.echo(f"  ! {line}")
    if plan.source.startswith("git-archive"):
        typer.echo(
            "\nNote: a git-archive bundle holds committed v1 data only. Uncommitted v1 changes\n"
            "(e.g. new lines in projects.conf) are not included — commit them in v1 and re-export."
        )
    elif plan.uncommitted:
        typer.echo(f"\n  Includes {plan.uncommitted} change(s) that were not committed in the source.")
    typer.echo("  Check it is complete any time:  corkern import <export> --check")
    if not dry_run:
        typer.echo(f"\nNext: export CORKERN_MEMORY_DIR={target}")
        typer.echo("corkern did not read or modify the v1 repo (only the bundle). Device wiring (init-device) comes after UAT.")
