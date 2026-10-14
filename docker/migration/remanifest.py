"""Test helper (scenarios2.sh): after a test changed or added files in an unpacked corkern export,
rewrite manifest.json so every checksum matches again — as if the export had been made that way.
Run inside the unpacked export folder."""
import hashlib
import json
from pathlib import Path

manifest = json.loads(Path("manifest.json").read_text())
listed = {f["path"]: f for f in manifest["files"]}
for p in sorted(Path(".").rglob("*")):
    rel = p.as_posix()
    if p.is_file() and rel != "manifest.json" and rel not in listed:
        listed[rel] = {"path": rel, "kind": "memory"}
for rel, entry in listed.items():
    data = Path(rel).read_bytes()
    entry.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
manifest["files"] = sorted(listed.values(), key=lambda f: f["path"])
Path("manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
