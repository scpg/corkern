"""graphify — build or register a project's knowledge graph (v1 `corkern graphify` parity).

Differences from v1, all for v1/machine safety:
- projects inside v1 or ~/.claude are refused by the guard
- `--install` is refused when it would write into a protected place (e.g. the `home`
  project, whose .claude/ is the user's global Claude settings)
- graphify extras are never installed automatically (graphify is shared with v1); the
  command to run is printed instead
- with no LLM API key, a code-only graph is built (`graphify update`, no LLM calls) instead
  of a build that would fail
"""
from __future__ import annotations

import os
from pathlib import Path

import typer

from corkern import guard
from corkern.commands.reminders import fail, slug_for_cwd
from corkern.config import check_projects, load_config

# Preference order matches v1; only ONE key is used (avoids backend ambiguity)
BACKENDS = [
    ("ANTHROPIC_API_KEY", "claude", "anthropic"),
    ("OPENAI_API_KEY", "openai", "openai"),
    ("GEMINI_API_KEY", "gemini", "gemini"),
    ("GOOGLE_API_KEY", "gemini", "gemini"),
    ("DEEPSEEK_API_KEY", "deepseek", "deepseek"),
    ("MOONSHOT_API_KEY", "kimi", "kimi"),
]


def _dotenv(project: Path) -> dict[str, str]:
    env_file = project / ".env"
    if not env_file.is_file():
        return {}
    values = {}
    for line in guard.read_text(env_file).split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _llm_key(project: Path) -> tuple[str, str, str, dict[str, str]] | None:
    """(key name, backend, uv extra, env to pass) — from the environment, else project .env."""
    for name, backend, extra in BACKENDS:
        if os.environ.get(name):
            return name, backend, extra, {}
    dotenv = _dotenv(project)
    for name, backend, extra in BACKENDS:
        if dotenv.get(name):
            return name, backend, extra, {name: dotenv[name]}  # value never printed
    return None


def graphify(
    slug: str = typer.Argument(None, help="Project slug (default: current project)"),
    install: bool = typer.Option(False, "--install", help="Register graphify with Claude Code"),
    full: bool = typer.Option(False, "--full", help="Force a complete rebuild"),
) -> None:
    """Build or update a project's knowledge graph (graphify)."""
    repo, projects = load_config()
    slug = slug or slug_for_cwd(projects)
    if not slug:
        fail("Could not detect project slug. Usage: corkern graphify [slug] [--install]\n"
             f"    Known slugs: {' '.join(sorted(projects))}")
    if slug not in projects:
        fail(f"Slug '{slug}' not found in corkern.conf")
    check = check_projects(repo.path)[slug]      # corkern.conf is shared: only a path confirmed here
    if not check.confirmed:
        fail(f"Refusing to run graphify in the folder of {check.explain()}")
    project = check.path
    if not project.is_dir():
        fail(f"Project directory not found: {project}")
    guard.assert_safe(project)                      # v1 repo, ~/.claude → hard stop
    exe = guard.which("graphify")
    if not exe:
        fail("graphify is not installed.\n    Install it with: uv tool install graphifyy")

    typer.echo(f"\ncorkern graphify: {slug}\n  Project: {project}")
    key = _llm_key(project)
    env: dict[str, str] = {}
    backend_args: list[str] = []
    if key:
        name, backend, extra, env = key
        backend_args = [f"--backend={backend}"]
        typer.echo(f"  ✓ Using {name} ({'project .env' if env else 'environment'}) → {backend_args[0]}")
        typer.echo(f"    If graphify reports a missing package: uv tool install 'graphifyy[{extra}]'")

    if install:
        try:
            guard.assert_safe(project / ".claude")
        except guard.V1ProtectedError:
            fail(f"Refusing --install for '{slug}': graphify would write {project / '.claude'}, "
                 "which is protected (global Claude settings).")
        typer.echo("  Registering graphify with Claude Code (CLAUDE.md + PreToolUse hook)...")
        if guard.run_tool(project, [exe, "claude", "install"], env) != 0:
            fail("graphify claude install failed")
        if guard.run_tool(project, [exe, "hook", "install"], env) == 0:
            typer.echo("  ✓ Git hooks installed — the graph rebuilds after commits and checkouts")
        else:
            typer.echo(f"  ! Git hook install failed (run: cd {project} && graphify hook install)")
        typer.echo(f"  ✓ Graphify registered for {slug}")
        return

    graph = project / "graphify-out" / "graph.json"
    incremental = graph.is_file() and not full
    if key:
        argv = [exe, ".", *(["--update"] if incremental else []), *backend_args]
        typer.echo(f"  Building knowledge graph ({'incremental' if incremental else 'full'})...")
    else:
        argv = [exe, "update", ".", *(["--force"] if full else [])]
        typer.echo("  ! No LLM API key found — building a code-only graph (no LLM calls).")
    rc = guard.run_tool(project, argv, env)
    if rc != 0:
        typer.echo(f"  ✗ graphify exited with code {rc}", err=True)
    if not graph.is_file():
        fail(f"Graph build failed — no graph.json produced (run: cd {project} && graphify . --verbose)")
    typer.echo(f"  ✓ Graph: {graph.parent}/")
    label = backend_args or ["--no-label"]
    if guard.run_tool(project, [exe, "cluster-only", ".", *label], env) != 0:
        typer.echo("  ! cluster-only failed — GRAPH_REPORT.md not generated")
    if rc != 0:
        raise typer.Exit(1)
