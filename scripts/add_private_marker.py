"""Add private markers (names, e-mail addresses, host names…) to the release gate.

    .venv/bin/python scripts/add_private_marker.py            # type one per line, empty line ends
    .venv/bin/python scripts/add_private_marker.py < markers   # or pipe them in, one per line

Markers are read from stdin only — never from the command line (shell history, process list) —
and are never printed or logged. Only their salted SHA-256 hashes are appended to
scripts/private_markers.json; the output says how many were added.

A marker is one token (word, e-mail address, host name, /home/<name>) or up to three words.
Markers the gate could never match (more than three words, or punctuation that splits the
token) are refused, so a stored hash always means a working check.
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_gate as gate  # noqa: E402


def read_markers() -> list[str]:
    if sys.stdin.isatty():
        out = []
        while marker := getpass.getpass("marker (empty line to finish): "):
            out.append(marker)
        return out
    return [line for line in sys.stdin.read().splitlines() if line.strip()]


def add(markers: list[str], path: Path | None = None) -> tuple[int, int, int]:
    """(added, already present, refused)."""
    path = path or gate.MARKERS_FILE
    data = json.loads(path.read_text())
    salt, hashes = data["salt"], list(data["hashes"])
    added = present = refused = 0
    for raw in markers:
        token = gate.normalize_marker(raw)
        if not token or len(token.split(" ")) > 3 or token not in gate.candidates(token):
            refused += 1
            continue
        h = gate.marker_hash(salt, token)
        if h in hashes:
            present += 1
        else:
            hashes.append(h)
            added += 1
    data["hashes"] = sorted(hashes)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return added, present, refused


def main() -> int:
    added, present, refused = add(read_markers())
    print(f"private markers: {added} added, {present} already present, {refused} refused "
          f"(more than three words, or not matchable as one token)")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
