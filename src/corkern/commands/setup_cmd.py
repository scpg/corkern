"""setup — give this machine its knowledge repo (once per machine, after installing corkern).

  corkern setup --clone git@github.com:<you>/<repo>.git   an existing knowledge repo (other machines)
  corkern setup --memory-dir PATH                          a knowledge repo already on this machine
  corkern setup --new                                      a new, empty knowledge repo
  corkern setup --create-github <owner>/<name>             new + published as a PRIVATE GitHub repo
  corkern setup --migrate EXPORT                           built from a corkern / v1 export

The location is recorded in this machine's machine.conf (~/.config/corkern/machine.conf), never
in ~/.claude, so dotfiles shared between machines stay machine-neutral. The hooks are installed
from the package. Then: corkern signing (asks), corkern init-device --apply (asks).
"""
from __future__ import annotations

from pathlib import Path

import typer

from corkern import guard, hooks_install
from corkern.commands.reminders import fail
from corkern.config import CONF_FILE, machine_conf_path, machine_memory_dir, safe_url, write_machine_conf
from corkern.knowledge_repo import InitError, harden, init_repo
from corkern.migrate import migrate as run_migrate



def setup(
    clone: str = typer.Option(None, "--clone", metavar="URL", help="Clone an existing knowledge repo"),
    new: bool = typer.Option(False, "--new", help="Create a new, empty knowledge repo"),
    create_github: str = typer.Option(None, "--create-github", metavar="OWNER/NAME",
                                      help="New knowledge repo, published as a PRIVATE GitHub repo (gh)"),
    migrate: Path = typer.Option(None, "--migrate", metavar="EXPORT", help="Build it from an export file"),
    memory_dir: Path = typer.Option(None, "--memory-dir", help="Where it lives (default ~/.corkern)"),
) -> None:
    """Set this machine's knowledge repo (machine.conf) and install the hooks."""
    modes = [m for m, on in (("clone", clone), ("new", new), ("github", create_github), ("migrate", migrate)) if on]
    if len(modes) > 1:
        fail("choose only one of --clone, --new, --create-github, --migrate")
    kr = guard.assert_safe(Path(memory_dir or Path.home() / ".corkern").expanduser().absolute())
    recorded = machine_memory_dir()
    if recorded and Path(recorded) != kr:
        fail(f"this machine already uses {recorded} (in {machine_conf_path()}).\n"
             f"  To change it, edit or remove that file first.")

    mode = modes[0] if modes else "existing"
    if mode == "existing":
        if not (kr / CONF_FILE).is_file():
            fail(f"no knowledge repo at {kr} — use --clone URL, --new, --create-github or --migrate")
        typer.echo(f"Using existing knowledge repo {kr}")
    elif kr.exists() and not guard.is_empty(kr):
        fail(f"{kr} exists and is not empty — choose another --memory-dir")

    try:
        if mode == "clone":
            guard.make_dirs(kr.parent)
            typer.echo(f"Cloning knowledge repo {safe_url(clone)} → {kr}")
            # no symlink is ever checked out, not even by the first checkout
            guard.run_git(kr.parent, "clone", "-q", "-c", "core.symlinks=false", "--", clone, str(kr), timeout=300)
            harden(kr)
            if not (kr / CONF_FILE).is_file():
                fail(f"{safe_url(clone)} is not a corkern knowledge repo (no {CONF_FILE}); the clone is left at {kr}")
        elif mode in ("new", "github"):
            init_repo(kr)
            typer.echo(f"Created knowledge repo {kr}")
            if mode == "github":
                typer.echo(f"Creating PRIVATE GitHub repo {create_github}")
                if guard.run_tool(kr, ["gh", "repo", "create", create_github, "--private",
                                       "--source", str(kr), "--remote", "origin", "--push"]) != 0:
                    fail("gh repo create failed — the knowledge repo is ready locally; publish it yourself")
        elif mode == "migrate":
            plan = run_migrate(migrate, kr)
            typer.echo(f"Migrated {len(plan.files)} file(s) from {migrate} → {kr}")
    except (InitError, guard.GitError, guard.BundleError) as e:
        fail(str(e))

    conf = write_machine_conf(kr)
    typer.echo(f"✓ this machine uses {kr}  (recorded in {conf})")
    try:
        typer.echo(f"✓ hooks in {hooks_install.install()}")
    except hooks_install.HooksError as e:
        typer.echo(f"! {e}")
    typer.echo("\nNext (each asks before changing anything):\n"
               "  corkern signing              this machine's signing (+ push) key\n"
               "  corkern init-device --apply  wire Claude Code (project memory, hooks, global CLAUDE.md)\n"
               "  corkern status               check everything")
