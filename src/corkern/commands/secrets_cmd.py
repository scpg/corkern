"""secrets — keep credentials out of the knowledge repo.

  corkern secrets                  what was removed before commit on this machine
  corkern secrets --scan           check the repo's files for secrets (read-only; exit 1 if any)
  corkern secrets --scan --history also check every version ever committed
  corkern secrets --redact F...    (used by the hooks) replace secrets in F... with a marker

The hooks call --redact on staged files that look suspicious, right before each commit: the
secret in the file is replaced by `[secret removed by corkern: <kind>]`, the original file is
kept on this machine only (state dir, mode 0600), and the redacted file is what gets
committed and pushed. Nothing in the quarantine is ever synced.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import typer

from corkern import guard
from corkern.commands.reminders import fail
from corkern.config import load_config, state_dir
from corkern.secret_scan import redact, scan

LOG = "secrets.log"
MAX_BYTES = 2 * 1024 * 1024


def quarantine_dir() -> Path:
    return state_dir() / "quarantine"


def _read(path: Path) -> str | None:
    """The file's text, or None: binary, over 2 MB, or a symlink in the knowledge repo (never
    followed — redacting it would rewrite whatever it points at)."""
    if guard.symlink_reason(path) or path.is_symlink():
        return None
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        return None
    try:
        return guard.read_text(path)
    except UnicodeDecodeError:
        return None


def redact_files(repo_path: Path, names: list[str]) -> tuple[list[str], list[str]]:
    """Redact secrets in place. Returns (changed, held): repo-relative paths that were redacted,
    and paths that could not be checked (binary or over 2 MB) — the hooks do not commit those."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    changed, held = [], []
    for name in names:
        path = Path(os.path.abspath(Path(name).expanduser()))
        if not path.is_relative_to(repo_path):
            typer.echo(f"skipped (outside the knowledge repo): {name}", err=True)
            continue
        text = _read(path)
        if text is None:
            if path.is_file() or path.is_symlink():
                held.append(str(path.relative_to(repo_path)))
            continue
        new, hits = redact(text)
        if not hits:
            continue
        rel = path.relative_to(repo_path)
        keep = quarantine_dir() / stamp / rel
        guard.write_private(keep, text)
        guard.write_text(path, new)
        kinds = ",".join(sorted({h.kind for h in hits}))
        guard.append_text(state_dir() / LOG,
                          f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{rel}\t{kinds}\t{keep}\n")
        changed.append(str(rel))
    return changed, held


def log_entries() -> list[list[str]]:
    path = state_dir() / LOG
    if not path.is_file():
        return []
    return [line.split("\t") for line in guard.read_text(path).splitlines() if line.count("\t") == 3]


def _report(where: str, text: str) -> int:
    hits = scan(text)
    for hit in hits:
        typer.echo(f"  ✗ {where}:{hit.line(text)}  {hit.kind}")
    return len(hits)


def _scan_files(repo_path: Path) -> int:
    listed = guard.run_git(repo_path, "-c", "core.quotePath=false", "ls-files", "-co",
                           "--exclude-standard").stdout.splitlines()
    found = 0
    for rel in listed:
        text = _read(repo_path / rel)
        if text is not None:
            found += _report(rel, text)
    return found


def _scan_history(repo_path: Path) -> int:
    """Added lines of every commit on every branch (what a clone of the remote contains)."""
    out = guard.run_git(repo_path, "-c", "core.quotePath=false", "log", "--all", "-p", "--no-color",
                        "--no-ext-diff", "--format=commit %h").stdout
    found, commit, file = 0, "", ""
    for line in out.splitlines():
        if line.startswith("commit "):
            commit = line[7:]
        elif line.startswith("+++ "):
            file = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+"):
            found += _report(f"{commit} {file}", line[1:])
    return found


def secrets(
    scan_repo: bool = typer.Option(False, "--scan", help="Check the knowledge repo's files (read-only)"),
    history: bool = typer.Option(False, "--history", help="With --scan: also every committed version"),
    redact_paths: list[str] = typer.Option(None, "--redact", help="(hooks) redact these files in place"),
    files: list[str] = typer.Argument(None, hidden=True),
) -> None:
    """Secrets kept out of the knowledge repo: removed ones, or --scan for any left."""
    repo, _ = load_config()
    if redact_paths:
        changed, held = redact_files(repo.path, [*redact_paths, *(files or [])])
        for rel in changed:
            typer.echo(rel)
        for rel in held:
            typer.echo(f"HOLD {rel}")             # read by hooks/lib.sh: do not commit these
        return

    if scan_repo:
        typer.echo(f"Scanning {repo.path} ...")
        found = _scan_files(repo.path)
        if history:
            typer.echo("History (every commit on every branch):")
            found += _scan_history(repo.path)
        if found:
            fail(f"{found} possible secret(s) found.\n"
                 "  In files: remove them, or let the next hook commit redact them.\n"
                 "  In history: rotate the secret (it may already be on the remote); removing it\n"
                 "  from history needs a history rewrite on every device (git filter-repo).")
        typer.echo("  ✓ No secrets found" + (" (files and history)" if history else " in the files"))
        return

    entries = log_entries()
    if not entries:
        typer.echo("  ✓ No secrets have been removed before commit on this machine.")
        typer.echo("  Check the repo itself with: corkern secrets --scan [--history]")
        return
    typer.echo("Secrets removed before commit (the redacted file was synced; the original stayed here):")
    for when, rel, kinds, keep in entries:
        typer.echo(f"  {when}  {rel}  ({kinds})\n      original: {keep}")
    typer.echo("\n  The originals contain the secret — delete them when you no longer need them.\n"
               "  If a secret was real, rotate it anyway: it existed in plain text on this machine.")
