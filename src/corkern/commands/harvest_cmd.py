"""harvest / refine / prune — memories from past Claude Code conversations (v1 parity).

  corkern harvest [slug] [--all] [-i]   extract memories from the project's conversation
                                         transcripts with Claude (new + changed ones; --all also
                                         re-processes harvested ones; -i: interactive session)
  corkern refine [slug]                 split harvested-*.md bulk files into memory files
  corkern prune [slug]                  conversation inventory: harvested / changed / pending

Transcripts are read from Claude Code's project folder ($CLAUDE_CONFIG_DIR or ~/.claude,
projects/<key>/*.jsonl) — read-only. Which ones were harvested, and where, is recorded in
memory/<slug>/.harvest-manifest (v1 format, synced, so every device sees it).

Differences from v1:
- a new memory never overwrites an existing file with the same name (v1 did, silently); it
  gets a `.harvested-<time>` suffix, and every new file is added to MEMORY.md
- choosing among several conversations works (v1 crashed: unset variable under `set -u`)
- the project folder is found like Claude Code names it (every non-alphanumeric → '-'; v1 only
  replaced '/', so paths with '.' or '_' were not found)
- refine moves a processed bulk file to archive/ instead of deleting it
- the prompt for -i (it contains the transcripts) is kept out of the knowledge repo, mode 0600
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import typer

from corkern import claude_print, guard
from corkern.commands.reminders import fail, slug_for_cwd
from corkern.commands.signing_cmd import device_name
from corkern.config import check_projects, load_config, state_dir

MAX_MSG = 2000
MAX_TOTAL = 300_000
MANIFEST = ".harvest-manifest"
FILE_BLOCK = re.compile(r"^=== FILE: (.+?) ===[ \t]*$", re.M)

FORMAT = """Output your findings as individual memory files using EXACTLY this format:

=== FILE: <descriptive-filename>.md ===
---
name: <short-kebab-slug>
description: <one-line summary>
metadata:
  type: <user|project|feedback|reference>
---

<body: lead with the fact/rule. For project/feedback types add **Why:** and **How to apply:** lines.>

=== FILE: <next-filename>.md ===
...

Rules:
- ONE block per distinct topic. Merge closely related items.
- Do NOT include any text outside the FILE blocks.
- Skip trivial exchanges and tool noise.
- Never write passwords, tokens or keys into a memory.
- If there is nothing worth persisting, output exactly: NO_MEMORIES

Memory types:
- user      : facts about the developer's role, expertise, preferences
- project   : decisions, goals, constraints, status for this project
- feedback  : guidance the user gave Claude (what to avoid/repeat and why)
- reference : pointers to external systems, URLs, config locations
"""


# ── transcripts ─────────────────────────────────────────────────────────────────────────
def extract(raw: bytes) -> str:
    """Readable USER/CLAUDE text of a .jsonl transcript (tool calls and results skipped)."""
    out, total = [], 0
    for line in raw.decode(errors="replace").splitlines():
        try:
            obj = json.loads(line) if line.strip() else None
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "\n".join(b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text")
        text = str(content).strip()
        if not text:
            continue
        if len(text) > MAX_MSG:
            text = text[:MAX_MSG] + "\n[...truncated...]"
        chunk = f"\n{'USER' if msg['role'] == 'user' else 'CLAUDE'}:\n{text}\n{'─' * 60}\n"
        if total + len(chunk) > MAX_TOTAL:
            out.append("\n[Transcript truncated — size limit reached]\n")
            break
        out.append(chunk)
        total += len(chunk)
    return "".join(out)


def split_blocks(output: str) -> list[tuple[str, str]]:
    """(filename, body) for each `=== FILE: name.md ===` block with a safe name."""
    parts = FILE_BLOCK.split(output)
    blocks = []
    for i in range(1, len(parts), 2):
        name, body = parts[i].strip(), (parts[i + 1] if i + 1 < len(parts) else "").strip()
        if not name or not body:
            continue
        if not name.endswith(".md") or "/" in name or "\\" in name or name.startswith("."):
            typer.echo(f"  skipped unsafe filename: {name}", err=True)
            continue
        blocks.append((name, body))
    return blocks


def _front(body: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+)$", body.split("\n---", 2)[0] if body.startswith("---") else "", re.M)
    return m.group(1).strip().strip('"') if m else ""


def write_memories(mem_dir: Path, blocks: list[tuple[str, str]]) -> list[str]:
    """Write each block as a memory file (never overwriting) and index it in MEMORY.md."""
    written, index_lines = [], []
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    for name, body in blocks:
        target = mem_dir / name
        if target.exists():
            target = mem_dir / f"{name[:-3]}.harvested-{stamp}.md"
            n = 2
            while target.exists():
                target = mem_dir / f"{name[:-3]}.harvested-{stamp}-{n}.md"
                n += 1
        guard.write_text(target, body + "\n")
        written.append(target.name)
        title = _front(body, "name") or target.stem
        desc = _front(body, "description")
        index_lines.append(f"- [{title}]({target.name})" + (f" — {desc}" if desc else "") + "\n")
    if index_lines:
        index = mem_dir / "MEMORY.md"
        text = guard.read_text(index) if index.is_file() else ""
        guard.write_text(index, text + ("" if not text or text.endswith("\n") else "\n") + "".join(index_lines))
    return written


# ── manifest ────────────────────────────────────────────────────────────────────────────
def read_manifest(mem_dir: Path) -> dict[str, list[str]]:
    path = mem_dir / MANIFEST
    if not path.is_file():
        return {}
    rows = [line.split("\t") for line in guard.read_text(path).splitlines() if line.count("\t") >= 3]
    return {r[0]: r for r in rows}


def record_manifest(mem_dir: Path, files: list[Path]) -> None:
    rows = read_manifest(mem_dir)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for f in files:
        rows[f.name] = [f.name, hashlib.sha256(guard.read_transcript(f)).hexdigest(), now, device_name()]
    guard.write_text(mem_dir / MANIFEST, "".join("\t".join(r[:4]) + "\n" for r in rows.values()))


def _classify(files: list[Path], manifest: dict[str, list[str]]) -> tuple[list[Path], list[Path], list[Path]]:
    new, changed, done = [], [], []
    for f in files:
        prev = manifest.get(f.name)
        if not prev:
            new.append(f)
        elif hashlib.sha256(guard.read_transcript(f)).hexdigest() != prev[1]:
            changed.append(f)
        else:
            done.append(f)
    return new, changed, done


# ── shared ──────────────────────────────────────────────────────────────────────────────
def _resolve(slug: str | None, usage: str, private: bool = False) -> tuple[object, str, Path]:
    """`private`: the command reads the project's conversations — only from a path this machine
    confirmed (corkern.conf is shared: a teammate could point the slug at another folder)."""
    repo, projects = load_config()
    slug = slug or slug_for_cwd(projects)
    if not slug:
        fail(f"Could not detect project slug.\n  Usage: {usage}\n  Known slugs: {' '.join(sorted(projects))}")
    if slug not in projects:
        fail(f"Slug '{slug}' not found in corkern.conf")
    if not private:
        return repo, slug, projects[slug]
    check = check_projects(repo.path)[slug]
    if not check.confirmed:
        fail(f"Refusing to read the conversations of {check.explain()}")
    if check.duplicate:
        typer.echo(f"  ! {slug}: {len(check.candidates)} different paths in corkern.conf ({', '.join(check.lines)})"
                   f" — using {check.path}, confirmed on this machine")
    return repo, slug, check.path


def _size(n: int) -> str:
    for unit, div in (("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n}B"


def _when(f: Path) -> str:
    return datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def _claude_or_fail() -> str:
    claude = guard.which("claude")
    if not claude:
        fail("'claude' CLI not found — install Claude Code.")
    return claude


def _run_claude(prompt: str, model: str | None) -> str:
    result = claude_print.ask(_claude_or_fail(), prompt, model)
    if result.returncode != 0:
        fail(f"Claude failed (exit {result.returncode}) — nothing was written or recorded.")
    return result.stdout or ""


def _ask_pick(count: int) -> list[int] | None:
    if count <= 1 or sys.stdin.closed:
        return None
    typer.echo("  Process which? [all] or space-separated numbers (e.g. 1 3): ", nl=False)
    choice = sys.stdin.readline().strip()
    typer.echo("")
    if not choice or choice == "all":
        return None
    return [int(n) - 1 for n in choice.split() if n.isdigit() and 0 < int(n) <= count]


# ── harvest ─────────────────────────────────────────────────────────────────────────────
def harvest(
    slug: str = typer.Argument(None, help="Project (default: the one for the current directory)"),
    all_: bool = typer.Option(False, "--all", help="Also re-process already harvested conversations"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Open an interactive Claude session instead"),
    model: str = typer.Option(None, "--model", help="Claude model"),
) -> None:
    """Extract memories from the project's past Claude Code conversations."""
    repo, slug, project = _resolve(slug, "corkern harvest [slug] [--all] [--interactive]", private=True)
    mem_dir = repo.memory_dir / slug
    typer.echo(f"\ncorkern harvest: {slug}\n  Project: {project}")
    files = guard.list_transcripts(project)
    if not files:
        source = guard.transcript_dir(project)
        typer.echo(f"  ! No conversations found in {source}\n  Has Claude Code been used in {project} on this machine?")
        return
    manifest = read_manifest(mem_dir)
    new, changed, done = _classify(files, manifest)
    todo: list[Path] = []
    for title, group in (("New conversations (not yet harvested):", new), ("Changed since last harvest:", changed)):
        if group:
            typer.echo(f"\n  {title}")
            for f in group:
                todo.append(f)
                typer.echo(f"  {len(todo):2d}. {f.name:<50}  {_size(f.stat().st_size):>6}  {_when(f)}")
    if done:
        typer.echo(f"\n  Already harvested ({len(done)}{'' if all_ else ' skipped'}):")
        for f in done:
            prev = manifest[f.name]
            if all_:
                todo.append(f)
                typer.echo(f"  {len(todo):2d}. {f.name:<50}  harvested {prev[2]} on {prev[3]} (--all: reprocessing)")
            else:
                typer.echo(f"    . {f.name:<50}  harvested {prev[2]} on {prev[3]}")
    if not todo:
        typer.echo("\n  ✓ All conversations already harvested. Use --all to reprocess.")
        return
    typer.echo(f"\n  {len(todo)} conversation(s) to process")
    picked = _ask_pick(len(todo))
    selected = [todo[i] for i in picked] if picked is not None else todo
    if not selected:
        typer.echo("  ! No files selected.")
        return

    prompt = (f'You are extracting persistent memory from past Claude Code conversation transcripts\n'
              f'for a project called "{slug}".\n\nYour goal: identify everything worth remembering for future '
              "sessions — key decisions,\nuser preferences, technical context, architectural choices, open questions, "
              "and\nanything that helps Claude Code pick up efficiently next time.\n\nThe user may add their own "
              "recollections after the transcripts. Treat those with\nequal weight.\n\n" + FORMAT
              + "\n---\nTRANSCRIPTS:\n")
    for f in selected:
        prompt += f"\n=== Conversation: {f.name} ({_when(f)}) ===\n\n{extract(guard.read_transcript(f))}"
    prompt += "\n\n---\nEND OF TRANSCRIPTS\n\nNow produce the memory files in === FILE: <name>.md === format as instructed above.\n"
    typer.echo(f"  ✓ Extracted {len(selected)} conversation(s) — prompt: {len(prompt.encode())} bytes\n")

    if interactive:
        claude = _claude_or_fail()
        prompt_file = state_dir() / "harvest-prompt.md"
        guard.write_private(prompt_file, prompt)
        record_manifest(mem_dir, selected)
        typer.echo(f"  Opening an interactive Claude session with the conversations as context.\n"
                   f"  Add your own recollections; ask Claude to write memory files to {mem_dir}/")
        guard.exec_tool([claude, "--append-system-prompt-file", str(prompt_file), *(["--model", model] if model else [])],
                        cwd=claude_print.interactive_cwd(project))

    typer.echo("  Running Claude extraction...")
    output = _run_claude(prompt, model)
    if re.search(r"^NO_MEMORIES$", output, re.M):
        typer.echo("  ! No memories worth persisting found in these conversations.")
    else:
        written = write_memories(mem_dir, split_blocks(output))
        if written:
            typer.echo(f"  ✓ Wrote {len(written)} memory file(s) to memory/{slug}/ (and indexed them in MEMORY.md):")
            for name in written:
                typer.echo(f"      {name}")
        else:
            bulk = mem_dir / f"harvested-{datetime.now():%Y-%m-%d}.md"
            guard.write_text(bulk, output)
            typer.echo(f"  ! Claude's answer had no parseable FILE blocks — saved for review: memory/{slug}/{bulk.name}\n"
                       f"    Run 'corkern refine {slug}' to reprocess it.")
    record_manifest(mem_dir, selected)
    typer.echo(f"  ✓ Manifest updated: {len(selected)} conversation(s) recorded as harvested on {device_name()}")
    typer.echo("  (Committed and synced by the next hook run.)\n")


# ── refine ──────────────────────────────────────────────────────────────────────────────
def refine(
    slug: str = typer.Argument(None, help="Project (default: the one for the current directory)"),
    model: str = typer.Option(None, "--model", help="Claude model"),
) -> None:
    """Split harvested-*.md bulk files into individual memory files (bulk file → archive/)."""
    repo, slug, _ = _resolve(slug, "corkern refine [slug]")
    mem_dir = repo.memory_dir / slug
    if not mem_dir.is_dir():
        fail(f"No memory directory for '{slug}'")
    bulk = sorted(mem_dir / name for name, _ in guard.list_files(mem_dir, ".md") if name.startswith("harvested-"))
    if not bulk:
        typer.echo(f"  ✓ No harvested-*.md bulk files found for '{slug}' — nothing to refine.")
        return
    typer.echo(f"\ncorkern refine: {slug}\n  Found {len(bulk)} bulk file(s) to refine:")
    for f in bulk:
        typer.echo(f"  • {f.name:<50}  {_size(f.stat().st_size)}")
    _claude_or_fail()
    total = 0
    for f in bulk:
        typer.echo(f"\n  Refining {f.name}...")
        prompt = (f'The following is a bulk harvest of extracted memories for project "{slug}".\n'
                  "Reformat it into individual corkern memory files.\n\n" + FORMAT
                  + "\n---\nBULK CONTENT:\n\n" + guard.read_text(f) + "\n\n---\nNow produce the === FILE: === blocks.\n")
        output = _run_claude(prompt, model)
        if re.search(r"^NO_MEMORIES$", output, re.M):
            dest = _archive(mem_dir, f)
            typer.echo(f"  ! {f.name}: no memories found — moved to archive/{dest.name}")
            continue
        written = write_memories(mem_dir, split_blocks(output))
        if written:
            dest = _archive(mem_dir, f)
            typer.echo(f"  ✓ {f.name}: wrote {len(written)} memory file(s) — bulk file moved to archive/{dest.name}")
            total += len(written)
        else:
            typer.echo(f"  ! {f.name}: no parseable FILE blocks in Claude's answer — bulk file kept")
    if total:
        typer.echo(f"\n  ✓ Done. {total} memory file(s) written to memory/{slug}/ (committed by the next hook run)\n")
    else:
        typer.echo("\n  ! No files were written.\n")


def _archive(mem_dir: Path, f: Path) -> Path:
    dest = mem_dir / "archive" / f"{datetime.now():%Y%m%d}-{f.name}"
    n = 2
    while dest.exists():
        dest = mem_dir / "archive" / f"{datetime.now():%Y%m%d}-{n}-{f.name}"
        n += 1
    guard.move(f, dest)
    return dest


# ── prune ───────────────────────────────────────────────────────────────────────────────
def prune(
    slug: str = typer.Argument(None, help="Project (default: the one for the current directory)"),
) -> None:
    """Conversation inventory for a project: harvested / changed / pending, and how to clean up."""
    repo, slug, project = _resolve(slug, "corkern prune <slug>", private=True)
    source = guard.transcript_dir(project)
    typer.echo(f"\ncorkern prune: {slug}\n  Project: {project}\n  Source: {source}\n")
    files = guard.list_transcripts(project)
    if not files:
        typer.echo("  ✓ No conversation files — nothing to prune.\n")
        return
    manifest = read_manifest(repo.memory_dir / slug)
    typer.echo(f"  {'#':<4} {'CONVERSATION':<42} {'SIZE':>7}  {'MODIFIED':<17}  {'STATUS':<10}  HARVESTED BY")
    typer.echo("  " + "─" * 110)
    harvested = pending = total = 0
    for i, f in enumerate(files, 1):
        size = f.stat().st_size
        total += size
        prev = manifest.get(f.name)
        if not prev:
            status, info = "pending", "—"
            pending += 1
        elif hashlib.sha256(guard.read_transcript(f)).hexdigest() != prev[1]:
            status, info = "changed", f"was: {prev[2]} on {prev[3]}"
            pending += 1
        else:
            status, info = "harvested", f"{prev[2]} on {prev[3]}"
            harvested += 1
        typer.echo(f"  {i:2d}. {f.name:<42} {_size(size):>7}  {_when(f):<17}  {status:<10}  {info}")
    typer.echo(f"\n  Total: {len(files)} conversations, {_size(total)}")
    typer.echo(f"  Status: {harvested} harvested, {pending} pending/changed")
    if pending:
        typer.echo(f"\n  pending / changed conversations still hold unharvested knowledge — run: corkern harvest {slug}")
    if harvested:
        typer.echo("\n  harvested conversations are safe to remove — their memories are already extracted.\n"
                   "  To remove them:\n"
                   "    rm <file>                                  (specific files)\n"
                   f"    claude project purge --dry-run {project}   (what Claude Code would delete)\n"
                   f"    claude project purge {project}             (ALL Claude Code state for the project;\n"
                   "                                               memories in the knowledge repo are not affected)")
    typer.echo("")
