"""iwe — the IWE knowledge graph (https://github.com/iwe-org/iwe) over the knowledge repo, as an
MCP server for Claude Code (v1: scripts/iwec-corkern.sh + an untracked .iwe/).

  corkern iwe              state: IWE installed? configured? MCP server registered?
  corkern iwe --setup      create .iwe/config.toml in the knowledge repo (`iwe init`, library
                            memory/) — committed and synced like everything else
  corkern iwe --install    register the MCP server "iwe" in Claude Code (asks first; refused
                            while v1 is installed — v1's own "iwe" server stays as it is)
  corkern iwe-serve        (what Claude Code runs) start `iwec` inside the knowledge repo
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from corkern import guard
from corkern.commands.reminders import fail
from corkern.commands.signing_cmd import _ask
from corkern.config import load_config

SERVER = "iwe"
CONFIG = Path(".iwe") / "config.toml"
INSTALL_HINT = ("install IWE 0.24+ (https://github.com/iwe-org/iwe/releases — or: cargo install iwe iwec)")


def _tool(name: str) -> str | None:
    found = guard.which(name)
    if found:
        return found
    cargo = Path.home() / ".cargo" / "bin" / name
    return str(cargo) if cargo.is_file() and os.access(cargo, os.X_OK) else None


def _self() -> str:
    return guard.which("corkern") or str(Path(sys.argv[0]).resolve())


def _registered(kr: Path) -> str | None:
    """The command Claude Code runs for the 'iwe' MCP server, if registered."""
    claude = guard.which("claude")
    if not claude:
        return None
    # user-scope registration: from $HOME, never from the knowledge repo (its .claude/ is shared)
    result = guard.capture_tool(Path.home(), [claude, "mcp", "get", SERVER], "", quiet=True)
    if result.returncode != 0:
        return None
    return next((line.split(":", 1)[1].strip() for line in (result.stdout or "").splitlines()
                 if line.strip().lower().startswith("command:")), "") or "(registered)"


def iwe(
    setup: bool = typer.Option(False, "--setup", help="Create .iwe/config.toml in the knowledge repo"),
    install: bool = typer.Option(False, "--install", help="Register the MCP server in Claude Code"),
    yes: bool = typer.Option(False, "--yes", help="Approve without asking"),
) -> None:
    """IWE knowledge graph over the knowledge repo, as an MCP server for Claude Code."""
    repo, _ = load_config()
    kr = repo.path
    iwe_bin, iwec_bin = _tool("iwe"), _tool("iwec")

    if setup:
        if not iwe_bin:
            fail(f"iwe not found — {INSTALL_HINT}")
        if (kr / CONFIG).is_file():
            typer.echo(f"  ✓ {kr / CONFIG} already exists")
        elif guard.run_tool(kr, [iwe_bin, "init", "--auto", "--library", "memory"]) != 0:
            fail("iwe init failed")
        typer.echo(f"  ✓ IWE configured for {kr}/memory (synced with the next hook commit)")
        return

    if install:
        if guard.v1_installed():
            fail("corkern v1 is installed on this machine and its 'iwe' MCP server points at the v1 repo.\n"
                 "  Register the v2 server only when switching to v2 (after UAT).")
        claude = guard.which("claude")
        if not claude:
            fail("claude (Claude Code) not found.")
        if not iwec_bin:
            fail(f"iwec not found — {INSTALL_HINT}")
        if not (kr / CONFIG).is_file():
            fail("No IWE configuration yet — run first: corkern iwe --setup")
        command = f"{_self()} iwe-serve"
        typer.echo(f"  Register MCP server '{SERVER}' in Claude Code (user scope): {command}\n"
                   f"    with CORKERN_MEMORY_DIR={kr}"
                   + ("\n    (replaces the existing 'iwe' server)" if _registered(kr) else ""))
        if not _ask("  Proceed? [y/N] ", yes):
            typer.echo(f"  Nothing changed. By hand:\n    claude mcp add {SERVER} --scope user "
                       f"-e CORKERN_MEMORY_DIR={kr} -- {command}")
            return
        if _registered(kr):
            guard.run_tool(Path.home(), [claude, "mcp", "remove", SERVER, "--scope", "user"])
        # name before -e: -e takes several values (real Claude Code reads "iwe" as one otherwise)
        if guard.run_tool(Path.home(), [claude, "mcp", "add", SERVER, "--scope", "user", "-e", f"CORKERN_MEMORY_DIR={kr}",
                               "--", _self(), "iwe-serve"]) != 0:
            fail("claude mcp add failed")
        typer.echo(f"  ✓ Registered. Check with: claude mcp list   (tools appear as mcp__{SERVER}__*)")
        return

    typer.echo(f"  {'✓' if iwe_bin else '✗'} iwe   {iwe_bin or INSTALL_HINT}")
    typer.echo(f"  {'✓' if iwec_bin else '✗'} iwec  {iwec_bin or '(the MCP server binary)'}")
    typer.echo(f"  {'✓' if (kr / CONFIG).is_file() else '✗'} {CONFIG} in the knowledge repo"
               + ("" if (kr / CONFIG).is_file() else " — run: corkern iwe --setup"))
    registered = _registered(kr)
    if registered and "iwe-serve" in registered:
        typer.echo(f"  ✓ MCP server '{SERVER}' registered: {registered}")
    elif registered:
        typer.echo(f"  ! MCP server '{SERVER}' points elsewhere ({registered})"
                   + (" — v1's, kept until the switch" if guard.v1_installed() else " — run: corkern iwe --install"))
    else:
        typer.echo(f"  ✗ MCP server '{SERVER}' not registered — run: corkern iwe --install")


def iwe_serve(args: list[str] = typer.Argument(None)) -> None:
    """(Claude Code runs this) the IWE MCP server inside the knowledge repo."""
    repo, _ = load_config()
    iwec_bin = _tool("iwec")
    if not iwec_bin:
        fail(f"iwec not found — {INSTALL_HINT}")
    guard.exec_tool([iwec_bin, *(args or [])], cwd=repo.path)
