"""metrics — memory corpus size, context pressure, growth (v1 parity).

Rows are appended to <repo>/.metrics.tsv at each session start (session-context), same
columns as v1: date, host, slug, mem_files, mem_bytes, ai_bytes, context_pct.
mem_bytes is the sum of memory file sizes (v1 used `du -sb`, which also counts directory
entries, so absolute numbers differ slightly between v1 and v2 rows).
"""
from __future__ import annotations

import socket
from datetime import date, datetime, timedelta

import typer

from corkern import guard
from corkern.config import MemoryRepo, load_config

HEADER = "date\thost\tslug\tmem_files\tmem_bytes\tai_bytes\tcontext_pct\n"
CONTEXT_BYTES = 800_000


def record(repo: MemoryRepo, slug: str, ai_text: str) -> None:
    files = guard.walk_files(repo.memory_dir, ".md")
    mem_bytes = sum(f.stat().st_size for f in guard.walk_files(repo.memory_dir))
    ai_bytes = len(ai_text.encode()) + 1          # + newline, like `corkern ai | wc -c`
    row = (f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{socket.gethostname()}\t{slug}\t{len(files)}"
           f"\t{mem_bytes}\t{ai_bytes}\t{ai_bytes * 100 // CONTEXT_BYTES}\n")
    path = repo.path / ".metrics.tsv"
    if not path.is_file():
        guard.write_text(path, HEADER)
    guard.append_text(path, row)


def _human(n: int, mb: bool = True) -> str:
    if mb and n >= 1048576:
        return f"{n // 1048576}.{(n % 1048576) * 10 // 1048576} MB"
    if n >= 1024:
        return f"{n // 1024} KB"
    return f"{n} B"


def metrics() -> None:
    """Memory corpus size, context pressure, and growth trends."""
    repo, _ = load_config()
    path = repo.path / ".metrics.tsv"
    rows = [line.split("\t") for line in guard.read_text(path).split("\n")[1:] if line] \
        if path.is_file() else []
    if not rows:
        typer.echo("  No metrics yet. Metrics are collected automatically on each session start.")
        return
    l_date, l_host, _slug, l_files, l_bytes, l_ai, l_ctx = rows[-1][:7]
    files, nbytes, ai, ctx = int(l_files), int(l_bytes), int(l_ai), int(l_ctx)
    typer.echo("Corkern Metrics\n")
    typer.echo(f"Current snapshot ({l_date}, {l_host})")
    typer.echo(f"  Memory files:     {files}")
    typer.echo(f"  Memory size:      {_human(nbytes)} ({nbytes} bytes)")
    typer.echo(f"  AI instructions:  {_human(ai, mb=False)} ({ai} bytes)")
    typer.echo(f"  Context pressure: {ctx}%")
    typer.echo("\nGrowth")
    for days, label in ((7, "7-day "), (30, "30-day")):
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        oldest = next((r for r in rows if r[0] >= cutoff), None)
        if oldest:
            d_files, d_bytes = files - int(oldest[3]), nbytes - int(oldest[4])
            typer.echo(f"  {label}:  {'+' if d_files >= 0 else ''}{d_files} files, "
                       f"{'+' if d_bytes >= 0 else ''}{d_bytes} bytes")
    typer.echo("\nAlerts")
    alerts = []
    if nbytes >= 524288:
        alerts.append(f"  ⚠ Memory corpus at {_human(nbytes)} — consider distill/prune to consolidate")
    if files >= 150:
        alerts.append(f"  ⚠ {files} memory files — consider graphify for memory querying")
    if ctx >= 25:
        alerts.append(f"  ⚠ Context pressure at {ctx}% — AI instructions consuming significant context")
    for line in alerts:
        typer.echo(line)
    if not alerts:
        typer.echo(f"  All healthy ({files} files, {_human(nbytes)}, {ctx}% context)")
    typer.echo("")
    typer.echo(f"Data points: {len(rows)} (collecting since {rows[0][0]})")
