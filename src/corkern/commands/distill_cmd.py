"""distill — synthesise cross-project insights from all memories with Claude (v1 parity).

  corkern distill           build the prompt, open an interactive Claude session with it
  corkern distill --auto    run Claude non-interactively → memory/global/synthesis.md

Fixes vs v1 distill.sh:
- the previous synthesis.md is only replaced when Claude succeeds (v1 truncated it before
  running Claude, so a failed run destroyed the last synthesis)
- no crash after saving (v1 referenced $BRAIN — a variable left over from the tool's old name
  "brain" that is never set — under `set -u`)
- the project list in the prompt comes from corkern.conf (v1 hard-coded it)
- the prompt file (a copy of every memory) is written to this machine's state dir (0600), never
  into the knowledge repo, where a synced symlink could redirect the write
"""
from __future__ import annotations

from datetime import datetime

import typer

from corkern import claude_print, guard
from corkern.commands.reminders import fail
from corkern.config import conf_entries, load_config, state_dir, valid_slug

PROMPT_NAME = ".distill-prompt.md"

HEADER = """You are analyzing a personal knowledge base ("corkern") that captures everything
Claude Code has learned working with this developer across multiple projects.

Memory files are organized as: memory/<project>/<topic>.md
Projects present: {projects}

Please produce a structured synthesis with these sections:

## Universal Principles
Rules or preferences that appear across 2+ projects (verbatim or in spirit).
For each: quote the source project(s) and note if it should be added to global/CLAUDE.md.

## Cross-Project Opportunities
Insights from one project that would benefit others where NOT yet recorded.

## Stale Candidates
Memory files that appear outdated, superseded, or about completed work.
For each: state why it may be stale.

## Contradictions
Rules or preferences that conflict between projects.
For each: surface the tension and suggest resolution.

## Who This Developer Is
A short paragraph synthesising working style, values, and instincts from all evidence combined.
Be specific — avoid generic statements.

---
MEMORY FILES:
"""


def build_prompt(repo) -> tuple[str, int]:
    slugs = list(dict.fromkeys(s for s, _ in conf_entries(repo.read_conf())
                               if valid_slug(s)))
    files = guard.walk_files(repo.memory_dir, ".md")
    parts = [HEADER.format(projects=", ".join([*slugs, "global"]))]
    for f in files:
        parts.append(f"\n=== {f.relative_to(repo.path)} ===\n{guard.read_text(f)}\n")
    return "".join(parts), len(files)


def distill(
    auto: bool = typer.Option(False, "--auto", help="Run non-interactively → memory/global/synthesis.md"),
    model: str = typer.Option(None, "--model", help="Claude model (default: Claude Code's default)"),
) -> None:
    """Synthesise cross-project insights from all memories with Claude."""
    repo, _ = load_config()
    typer.echo("=== Corkern Distill ===\n\nCollecting memories...")
    prompt, count = build_prompt(repo)
    prompt_file = state_dir() / PROMPT_NAME
    guard.write_private(prompt_file, prompt)
    typer.echo(f"✓ Collected {count} files ({len(prompt.encode())} bytes).\n")
    claude = guard.which("claude")
    model_args = ["--model", model] if model else []

    if auto:
        if not claude:
            fail("'claude' CLI not found — install Claude Code or run without --auto.")
        typer.echo("Running Claude analysis...")
        result = claude_print.ask(claude, prompt, model)
        if result.returncode != 0 or not result.stdout.strip():
            fail(f"Claude failed (exit {result.returncode}) — previous synthesis.md left unchanged.")
        now = datetime.now()
        front = (f"---\nname: synthesis\ndescription: Cross-project synthesis — {now:%Y-%m-%d}\n"
                 f"metadata:\n  type: reference\n  generated: {now:%Y-%m-%dT%H:%M}\n---\n\n")
        guard.write_text(repo.memory_dir / "global" / "synthesis.md", front + result.stdout)
        typer.echo("✓ Saved: memory/global/synthesis.md")
        typer.echo("  (Committed and synced by the next corkern hook run.)")
        return

    typer.echo(f"Prompt saved: {prompt_file}\n")
    if not claude:
        typer.echo(f"Claude CLI not found. To analyse manually, paste {prompt_file} into any Claude session.")
        return
    typer.echo("Starting interactive Claude analysis session...\n"
               "(Claude will open with all your memories as context.)\n")
    # from where the user is — unless that is inside the knowledge repo (its .claude/ is shared)
    guard.exec_tool([claude, "--append-system-prompt-file", str(prompt_file), *model_args],
                    cwd=claude_print.interactive_cwd())
