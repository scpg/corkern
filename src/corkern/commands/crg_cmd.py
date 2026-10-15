"""crg — build or install code-review-graph for a project (v1 `corkern crg` parity).

Same safety rules as graphify: projects inside v1 / ~/.claude are refused, and --install is
refused when it would write a protected place (code-review-graph install writes the
project's .claude/settings.json, .mcp.json and CLAUDE.md).
"""
from __future__ import annotations

import typer

from corkern import guard
from corkern.commands.reminders import fail, slug_for_cwd
from corkern.config import check_projects, load_config

TOOL = "code-review-graph"


def crg(
    slug: str = typer.Argument(None, help="Project slug (default: current project)"),
    install: bool = typer.Option(False, "--install", help="Install MCP server, hooks, CLAUDE.md"),
) -> None:
    """Build/update the project's code-review-graph (fast, AST-only, no LLM)."""
    repo, projects = load_config()
    slug = slug or slug_for_cwd(projects)
    if not slug:
        fail("Could not detect project slug. Usage: corkern crg [slug] [--install]\n"
             f"    Known slugs: {' '.join(sorted(projects))}")
    if slug not in projects:
        fail(f"Slug '{slug}' not found in corkern.conf")
    check = check_projects(repo.path)[slug]      # corkern.conf is shared: only a path confirmed here
    if not check.confirmed:
        fail(f"Refusing to run code-review-graph in the folder of {check.explain()}")
    project = check.path
    if not project.is_dir():
        fail(f"Project directory not found: {project}")
    guard.assert_safe(project)
    exe = guard.which(TOOL)
    if not exe:
        fail(f"{TOOL} is not installed.\n    Install it with: pipx install {TOOL}")

    typer.echo(f"\ncorkern crg: {slug}\n  Project: {project}")
    if install:
        try:
            guard.assert_safe(project / ".claude")
        except guard.V1ProtectedError:
            fail(f"Refusing --install for '{slug}': {TOOL} would write {project / '.claude'}, "
                 "which is protected (global Claude settings).")
        if guard.run_tool(project, [exe, "install"]) != 0:
            fail(f"{TOOL} install failed")
        typer.echo(f"  ✓ {TOOL} installed (MCP server + hooks + CLAUDE.md) — restart Claude Code")
        return

    db = project / ".code-review-graph"
    typer.echo(f"  {'Updating' if db.exists() else 'Building'} the graph (AST only, no LLM)...")
    rc = guard.run_tool(project, [exe, "build", "--repo", str(project)])
    if rc != 0:
        fail(f"{TOOL} build exited with code {rc}")
    typer.echo(f"  ✓ Graph: {db}")
    if not (project / ".mcp.json").exists():
        typer.echo(f"  Next step — register it with Claude Code: corkern crg {slug} --install")
