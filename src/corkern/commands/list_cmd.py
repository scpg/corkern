"""list — every project on every device (v1 `corkern list` layout, from .devices manifests).

  corkern list                          SLUG DEVICE PATH STATUS LAST-SEEN
  corkern list -s DEVICE -s SLUG        sort (repeatable: first key wins)
  corkern list -c SLUG,PATH             only these columns, in this order

This device's rows come from the registry (so they are always current); other devices' rows
from their manifests. STATUS on this device says whether Claude Code's memory for the project
points at the knowledge repo (`corkern init-device`); other devices show "registered".
"""
from __future__ import annotations

import json
import locale
from pathlib import Path

import typer

from corkern import devices, guard
from corkern.commands.signing_cmd import device_name
from corkern.config import NOT_CONFIRMED, check_projects, load_config

COLUMNS = ("SLUG", "DEVICE", "PATH", "STATUS", "LAST-SEEN")
FIELD = {"SLUG": 0, "DEVICE": 1, "PATH": 3, "STATUS": 4, "LAST-SEEN": 5}


def _collate(text: str) -> str:
    """Sort key in the user's locale (case-insensitive like `sort`), plain order without one."""
    try:
        locale.setlocale(locale.LC_COLLATE, "")
        return locale.strxfrm(text)
    except locale.Error:
        return text


def local_status(project: Path, target: Path) -> str:
    if project == Path.home():                    # Claude Code's own ~/.claude/settings.local.json
        try:
            text = guard.read_claude_config("settings.local.json")
        except guard.V1ProtectedError:
            return "v1 location"
    else:
        try:
            guard.assert_safe(project)
        except guard.V1ProtectedError:
            return "v1 location"
        if not project.is_dir():
            return "no path"
        settings = project / ".claude" / "settings.local.json"
        text = guard.read_text(settings) if settings.is_file() else None
    if text is None:
        return "not wired"
    try:
        value = json.loads(text or "{}").get("autoMemoryDirectory")
    except (json.JSONDecodeError, AttributeError):
        return "?"
    if not value:
        return "not wired"
    return "synced" if Path(value).expanduser() == target else "other dir"


def _rows(repo, projects) -> list[tuple[str, ...]]:
    me = device_name()
    manifests = devices.read_all(repo)
    names = list(manifests) + ([me] if me not in manifests else [])
    rows = []
    for slug in devices.ordered_slugs(repo, projects):
        for device in names:
            if device == me:
                last = manifests.get(me, {}).get(slug, ("", ""))[0]
                status = local_status(projects[slug], repo.memory_dir / slug)
                rows.append((slug, f"{device} (local)", "1", str(projects[slug]), status, last))
            elif slug in manifests[device] and manifests[device][slug][1]:
                last, path = manifests[device][slug]
                rows.append((slug, device, "0", path, "registered", last))
    return rows


def list_projects(
    sort: list[str] = typer.Option(None, "--sort", "-s", help="Sort by column (repeatable)"),
    columns: str = typer.Option(",".join(COLUMNS), "--columns", "-c",
                                help="Comma-separated columns to show"),
) -> None:
    """List projects on every device: SLUG DEVICE PATH STATUS LAST-SEEN."""
    repo, projects = load_config()
    rows = _rows(repo, projects)
    keys = [FIELD[k.upper()] for k in (sort or []) if k.upper() in FIELD]
    if keys:                                   # like v1's `sort -t$'\t' -k…`: locale order,
        rows.sort(key=lambda r: (*(_collate(r[i]) for i in keys), _collate("\t".join(r))))
    show = [c.upper() for c in columns.split(",") if c.upper() in COLUMNS]
    path_w = max([44, *(len(r[3]) for r in rows)])         # v1 cut paths at 44; v2 shows them whole
    widths = {"SLUG": 17, "DEVICE": 19, "PATH": path_w + 1, "STATUS": 15, "LAST-SEEN": 19}
    header = "".join(f"{c:<{widths[c] - 1}} " if c != "LAST-SEEN" else c for c in show)
    typer.echo(f"\nProjects\n  {header}\n  {'─' * sum(widths[c] for c in show)}")
    for r in rows:
        values = {"SLUG": f"{r[0]:<16} ", "DEVICE": f"{r[1]:<18} ",
                  "PATH": f"{r[3]:<{path_w}} ",
                  "STATUS": f"{r[4]:<14} ", "LAST-SEEN": r[5]}
        typer.echo("  " + "".join(values[c] for c in show))
    if not rows:
        typer.echo("  No projects tracked yet — add one with: corkern add <slug> [path]")
    for c in check_projects(repo.path).values():
        if not c.confirmed:
            typer.echo(f"  ! {c.slug}: {NOT_CONFIRMED} — corkern add {c.slug} <path> (or corkern add --confirm-all)")
    typer.echo("")
