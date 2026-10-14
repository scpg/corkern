import os
import sys
from typing import Annotated, Optional
import typer
from corkern import __version__
from corkern.commands.list_cmd import list_projects
from corkern.commands.add_cmd import add_project
from corkern.commands.remove_cmd import remove_project
from corkern.commands.status_cmd import doctor, status
from corkern.commands.init_cmd import init
from corkern.commands.migrate_cmd import migrate
from corkern.commands.reminders import done, remind, todos
from corkern.commands.sessions_cmd import log, sessions
from corkern.commands.ai_cmd import get_ai_instructions, session_context
from corkern.commands.graphify_cmd import graphify
from corkern.commands.prune_cmd import memory_prune
from corkern.commands.metrics_cmd import metrics
from corkern.commands.distill_cmd import distill
from corkern.commands.crg_cmd import crg
from corkern.commands.signing_cmd import signing
from corkern.commands.secrets_cmd import secrets
from corkern.commands.trust_cmd import trust
from corkern.commands.init_device_cmd import init_device, install_hooks
from corkern.commands.setup_cmd import setup
from corkern.commands.iwe_cmd import iwe, iwe_serve
from corkern.commands.harvest_cmd import harvest, prune, refine
from corkern.commands.export_cmd import export, import_

app = typer.Typer(
    name="corkern", help="Memory sync system for Claude Code.", pretty_exceptions_enable=False,
)


def _version_cb(value: bool) -> None:
    if value:
        typer.echo(f"corkern {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", "-V", callback=_version_cb, is_eager=True, help="Show version."),
    ] = None,
) -> None:
    pass


app.command("list")(list_projects)
app.command("add")(add_project)
app.command("remove")(remove_project)
app.command("status")(status)
app.command("setup")(setup)
app.command("init")(init)
app.command("migrate")(migrate)
app.command("remind")(remind)
app.command("done")(done)
app.command("todos")(todos)
app.command("sessions")(sessions)
app.command("log")(log)
app.command("graphify")(graphify)
app.command("memory-prune")(memory_prune)
app.command("metrics")(metrics)
app.command("distill")(distill)
app.command("crg")(crg)
app.command("signing")(signing)
app.command("secrets")(secrets)
app.command("trust")(trust)
app.command("init-device")(init_device)
app.command("install-hooks")(install_hooks)
app.command("iwe")(iwe)
app.command("harvest")(harvest)
app.command("refine")(refine)
app.command("prune")(prune)
app.command("export")(export)
app.command("import")(import_)
app.command("iwe-serve", hidden=True)(iwe_serve)
app.command("get-AIinstructions")(get_ai_instructions)
app.command("session-context", hidden=True)(session_context)

# v1 aliases
app.command("ls", hidden=True)(list_projects)
app.command("rm", hidden=True)(remove_project)
app.command("todo", hidden=True)(todos)
app.command("doctor")(doctor)
app.command("ai", hidden=True)(get_ai_instructions)
app.command("context", hidden=True)(get_ai_instructions)


def main() -> None:
    """Entry point. Unexpected errors become one clear line instead of a traceback;
    set CORKERN_DEBUG=1 to see the full traceback."""
    from corkern import instance
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    command = args[0] if args else ""
    read_only = command in ("", "doctor", "status") or "--version" in sys.argv or "--help" in sys.argv
    if not read_only and not instance.is_central():
        print(f"ERROR: this corkern ({sys.prefix}) is not the machine's tool install and refuses to act.\n"
              "  corkern must be installed once per user: uv tool install corkern (or pipx install corkern).\n"
              "  A copy in a project environment would act next to it. Check: corkern doctor",
              file=sys.stderr)
        sys.exit(1)
    try:
        app()
    except Exception as e:  # SystemExit (incl. the v1 guard) is not an Exception — passes through
        if os.environ.get("CORKERN_DEBUG"):
            raise
        print(f"ERROR: {type(e).__name__}: {e}\n"
              "This is a bug in corkern — rerun with CORKERN_DEBUG=1 for details.",
              file=sys.stderr)
        sys.exit(1)
