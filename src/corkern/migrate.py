"""v1 → v2 migration from an export bundle.

v2 never reads the v1 repo directly. The user exports committed v1 data with:

    git -C ~/dev/corkern archive HEAD memory .devices global projects.conf > ~/v1-export.tar

and migrate builds a new knowledge repo from that tarball alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from corkern import guard
from corkern.config import CONF_FILE
from corkern.knowledge_repo import TEMPLATE, InitError, require_dir_or_absent

V1_REGISTRY = "projects.conf"
MIGRATE_COMMIT_MSG = "corkern: migrate from v1 export bundle"


@dataclass
class Plan:
    files: dict[str, bytes]
    skipped: list[str] = field(default_factory=list)
    source: str = "git-archive bundle (committed v1 data only)"
    uncommitted: int = 0

    @property
    def memory_files(self) -> int:
        return sum(1 for f in self.files if f.startswith("memory/"))

    @property
    def devices(self) -> int:
        return sum(1 for f in self.files if f.startswith(".devices/"))

    @property
    def projects(self) -> int:
        conf = self.files.get(CONF_FILE, b"").decode(errors="replace")
        return sum(1 for l in conf.splitlines() if ":" in l and not l.strip().startswith("#"))


def plan_from_export(bundle: Path) -> Plan:
    """A corkern-export file (v1's `corkern export` or v2's): verified first, data only."""
    from corkern.export_format import ExportError, read
    try:
        exp = read(bundle)
    except ExportError as e:
        raise guard.BundleError(str(e))
    files: dict[str, bytes] = {}
    skipped: list[str] = []
    registry = "corkern.conf" if exp.layout == "v2" else V1_REGISTRY
    for rel, data in exp.files("data").items():
        if rel == registry:
            files[CONF_FILE] = data
        elif rel.startswith(("memory/", ".devices/", "global/")) or rel == ".metrics.tsv":
            files[rel] = data
        else:
            skipped.append(f"data/{rel}")
    skipped += [n for n in exp.members if not n.startswith("data/")]
    if not any(f.startswith("memory/") for f in files):
        raise guard.BundleError(f"{bundle} has no memory/ entries")
    plan = Plan(files, sorted(skipped))
    plan.source = f"{exp.manifest.get('tool')} export from {exp.host}, {exp.manifest.get('created')}"
    plan.uncommitted = len(exp.manifest.get("repo", {}).get("uncommitted") or [])
    return plan


def plan_from_bundle(bundle: Path) -> Plan:
    from corkern.export_format import is_export
    if is_export(bundle):
        return plan_from_export(bundle)
    entries, skipped = guard.read_tar(bundle)
    files: dict[str, bytes] = {}
    for name, data in entries.items():
        if name == V1_REGISTRY:
            files[CONF_FILE] = data
        elif name.startswith(("memory/", ".devices/")) or name == "global/CLAUDE.md":
            files[name] = data
        else:
            skipped.append(name)
    if not any(f.startswith("memory/") for f in files):
        raise guard.BundleError(f"{bundle} has no memory/ entries — is this a v1 export?")
    return Plan(files, sorted(skipped))


def migrate(bundle: Path, dest: Path, dry_run: bool = False) -> Plan:
    target = guard.assert_safe(dest)
    require_dir_or_absent(target)
    if not guard.is_empty(target):
        raise InitError(f"{target} is not empty — migrate needs a new, empty destination.")
    plan = plan_from_bundle(bundle)
    # any text file (memories, registry, metrics) — a half-merged file must never become v2's start
    marked = [rel for rel, data in plan.files.items() if b"\0" not in data
              if any(line.startswith((b"<<<<<<< ", b">>>>>>> ")) for line in data.splitlines())]
    if marked:
        raise guard.BundleError(f"conflict markers in {', '.join(sorted(marked))} — resolve them in the source "
                                "(keep both sides) and export again")
    if dry_run:
        return plan

    for rel, content in TEMPLATE.items():
        if rel not in plan.files:
            guard.write_text(target / rel, content)
    for rel, data in plan.files.items():
        guard.write_bytes(target / rel, data)
    guard.run_git(target, "init", "-q", "-b", "main")
    guard.run_git(target, "add", "-A")
    guard.run_git(target, "commit", "-q", "-m", MIGRATE_COMMIT_MSG)
    return plan
