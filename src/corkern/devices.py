"""Device manifests — `.devices/<device>`: which projects a device has, where, and when they
were last used there (v1 format, so v1 and v2 manifests read the same).

    # corkern device manifest — auto-generated
    # format: slug:last-seen:path
    host:<device>
    <slug>:<YYYY-MM-DD HH:MM:SS>:<absolute path>

Each device writes only its own file (no merge conflicts); the session-start context refreshes
it and stamps the current project. Older v1 manifests ("slug:path" + "updated:" line) are read
too.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from corkern import guard
from corkern.commands.signing_cmd import device_name
from corkern.config import MemoryRepo, conf_entries, valid_slug

DIR = ".devices"
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def parse(text: str) -> dict[str, tuple[str, str]]:
    """slug → (last-seen, path); first line per slug wins (like v1's grep | head -1)."""
    updated = next((line.split(":", 1)[1] for line in text.splitlines() if line.startswith("updated:")), "")
    entries: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or line.startswith(("host:", "updated:")):
            continue
        slug, sep, rest = line.partition(":")
        if not sep or slug in entries:
            continue
        parts = rest.split(":")
        stamp = ":".join(parts[:3])
        if len(parts) >= 4 and STAMP.match(stamp):
            entries[slug] = (stamp, ":".join(parts[3:]))
        else:
            entries[slug] = (updated, rest)
    return entries


def read_all(repo: MemoryRepo) -> dict[str, dict[str, tuple[str, str]]]:
    folder = repo.path / DIR
    return {name: parse(guard.read_text(folder / name))
            for name, _ in sorted(guard.list_files(folder)) if name != "allowed_signers"}


def ordered_slugs(repo: MemoryRepo, projects: dict[str, Path]) -> list[str]:
    """Registry order (corkern.conf), then slugs only defined in corkern.conf.local."""
    order = list(dict.fromkeys(s for s, _ in conf_entries(repo.read_conf())
                               if valid_slug(s) and s in projects))
    return order + sorted(s for s in projects if s not in order)


def update(repo: MemoryRepo, projects: dict[str, Path], touch: str | None = None) -> Path:
    """Rewrite this device's manifest from the registry, keeping last-seen stamps."""
    me = device_name()
    path = repo.path / DIR / me
    if path.is_symlink():          # a synced link would make us write wherever it points
        guard.remove_symlink(path)
    previous = parse(guard.read_shared(path) or "")
    now = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    lines = ["# corkern device manifest — auto-generated", "# format: slug:last-seen:path", f"host:{me}"]
    for slug in ordered_slugs(repo, projects):
        stamp = now if slug == touch else (previous.get(slug, ("", ""))[0] or now)
        lines.append(f"{slug}:{stamp}:{projects[slug]}")
    content = "\n".join(lines) + "\n"
    if guard.read_shared(path) != content:
        guard.write_text(path, content)
    return path
