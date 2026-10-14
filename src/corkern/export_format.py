"""corkern-export, version 1 — the file format shared by v1 (`corkern export`) and v2
(`corkern export`): see docs/export-format.md. Reading always verifies before anything is used.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from corkern import guard

FORMAT = "corkern-export"
VERSION = 1


class ExportError(ValueError):
    pass


@dataclass
class Export:
    manifest: dict
    members: dict[str, bytes]

    @property
    def layout(self) -> str:
        """"v1" (projects.conf registry) or "v2" (corkern.conf)."""
        return "v2" if str(self.manifest.get("tool", "")).startswith("corkern v2") else "v1"

    @property
    def host(self) -> str:
        return str(self.manifest.get("host", "unknown"))

    def files(self, prefix: str) -> dict[str, bytes]:
        """Members under `prefix/`, keyed by their path without it."""
        return {name[len(prefix) + 1:]: data for name, data in self.members.items() if name.startswith(prefix + "/")}

    def registry_text(self) -> str:
        data = self.files("data")
        raw = data.get("corkern.conf") if self.layout == "v2" else data.get("projects.conf")
        return (raw or b"").decode(errors="replace")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_export(path: Path) -> bool:
    try:
        files, _ = guard.read_tar(path)
    except guard.BundleError:
        return False
    return "manifest.json" in files


def read(path: Path) -> Export:
    """Load and verify: format, version, every checksum, nothing unlisted. Raises ExportError."""
    try:
        files, skipped = guard.read_tar(path)
    except guard.BundleError as e:
        raise ExportError(str(e))
    if skipped:
        raise ExportError(f"unexpected non-file entries: {', '.join(skipped[:5])}")
    if "manifest.json" not in files:
        raise ExportError(f"{path} has no manifest.json — not a corkern export")
    try:
        manifest = json.loads(files.pop("manifest.json"))
    except ValueError as e:
        raise ExportError(f"manifest.json is not valid JSON: {e}")
    if manifest.get("format") != FORMAT:
        raise ExportError(f"format is {manifest.get('format')!r}, expected {FORMAT!r}")
    if int(manifest.get("format_version", 0)) > VERSION:
        raise ExportError(f"export format version {manifest['format_version']} is newer than this corkern "
                          f"understands ({VERSION}) — update corkern")
    listed = {f["path"]: f for f in manifest.get("files", [])}
    problems = [f"missing in the archive: {p}" for p in listed if p not in files]
    problems += [f"checksum mismatch: {p}" for p, f in listed.items() if p in files and sha256(files[p]) != f["sha256"]]
    problems += [f"not in the manifest: {p}" for p in files if p not in listed]
    if problems:
        raise ExportError("the export is damaged — " + "; ".join(problems[:5]))
    return Export(manifest, files)


def write(path: Path, manifest: dict, members: dict[str, tuple[bytes, str]]) -> Export:
    """Write members {path: (content, kind)} with a manifest, then read it back and verify."""
    manifest = {**manifest, "format": FORMAT, "format_version": VERSION,
                "files": [{"path": p, "kind": k, "size": len(d), "sha256": sha256(d)}
                          for p, (d, k) in sorted(members.items())]}
    entries = [("manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())]
    entries += [(p, d) for p, (d, _) in sorted(members.items())]
    guard.write_tar_private(path, entries)
    return read(path)
