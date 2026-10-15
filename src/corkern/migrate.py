"""v1 → v2 migration from an export bundle.

v2 never reads the v1 repo directly. The user exports committed v1 data with:

    git -C ~/dev/corkern archive HEAD memory .devices global projects.conf > ~/v1-export.tar

and migrate builds a new knowledge repo from that tarball alone.

Nothing in the bundle is trusted: a member with a ".git" component (or one of its NTFS/HFS
forms) is never written, and the migration commit is made only after the hooks' secret scan
and redaction (originals to the local quarantine). An export holds working-tree files, i.e.
text no hook has scanned yet. When the scan cannot run, nothing is committed (the command exits
1); the files stay in place and the same command, run again, finishes that migration.

The projects of the export are confirmed on this machine (.git/corkern/projects-confirmed):
the export is this user's own registry, not a teammate's later change to the shared one.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from corkern import guard
from corkern.config import CONF_FILE, _parse_conf, confirm_projects, repo_state_dir
from corkern.knowledge_repo import TEMPLATE, InitError, require_dir_or_absent, stage
from corkern.secret_scan import HINT

V1_REGISTRY = "projects.conf"
MIGRATE_COMMIT_MSG = "corkern: migrate from v1 export bundle"
MAX_SCAN_BYTES = 2 * 1024 * 1024        # as `corkern secrets` (the hooks): larger files are not checked
_HINT = re.compile(HINT.encode(), re.IGNORECASE)


@dataclass
class Plan:
    files: dict[str, bytes]
    skipped: list[str] = field(default_factory=list)
    source: str = "git-archive bundle (committed v1 data only)"
    uncommitted: int = 0
    refused: list[tuple[str, str]] = field(default_factory=list)   # (path, why): never written
    redacted: list[str] = field(default_factory=list)              # secrets removed before the commit
    unscanned: str = ""                                            # why nothing was committed

    def refuse(self, rel: str) -> bool:
        why = guard.unsafe_member(rel)
        if why:
            self.refused.append((rel, why))
        return bool(why)

    def report(self) -> list[str]:
        """What the user must know about refused files and the secret scan."""
        from corkern.commands.secrets_cmd import quarantine_dir
        lines = [f"skipped: {rel} — {why}" for rel, why in self.refused]
        if self.redacted:
            lines.append(f"secrets removed from {len(self.redacted)} file(s) before the migration commit "
                         f"(originals on this machine only: {quarantine_dir()}): {', '.join(self.redacted)}")
        if self.unscanned:
            lines.append(f"not committed: the secret scan could not run ({self.unscanned}). The files are in "
                         "place, uncommitted; the first hook commit scans them (or: corkern secrets --scan)")
        return lines

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
    plan = Plan(files)
    for rel, data in exp.files("data").items():
        if plan.refuse(rel):
            continue
        if rel == registry:
            files[CONF_FILE] = data
        elif rel.startswith(("memory/", ".devices/", "global/")) or rel == ".metrics.tsv":
            files[rel] = data
        else:
            skipped.append(f"data/{rel}")
    skipped += [n for n in exp.members if not n.startswith("data/")]
    if not any(f.startswith("memory/") for f in files):
        raise guard.BundleError(f"{bundle} has no memory/ entries")
    plan.skipped = sorted(skipped)
    plan.source = f"{exp.manifest.get('tool')} export from {exp.host}, {exp.manifest.get('created')}"
    plan.uncommitted = len(exp.manifest.get("repo", {}).get("uncommitted") or [])
    return plan


def plan_from_bundle(bundle: Path) -> Plan:
    from corkern.export_format import is_export
    if is_export(bundle):
        return plan_from_export(bundle)
    entries, skipped = guard.read_tar(bundle)
    files: dict[str, bytes] = {}
    plan = Plan(files)
    for name, data in entries.items():
        if plan.refuse(name):
            continue
        if name == V1_REGISTRY:
            files[CONF_FILE] = data
        elif name.startswith(("memory/", ".devices/")) or name == "global/CLAUDE.md":
            files[name] = data
        else:
            skipped.append(name)
    if not any(f.startswith("memory/") for f in files):
        raise guard.BundleError(f"{bundle} has no memory/ entries — is this a v1 export?")
    plan.skipped = sorted(skipped)
    return plan


def unscannable(data: bytes) -> bool:
    """A file `corkern secrets` cannot check (over 2 MB or not UTF-8) that mentions a secret
    word: the hooks hold such a file back (\"could not be checked\"), so it is not imported."""
    try:
        data.decode("utf-8")
        readable = len(data) <= MAX_SCAN_BYTES
    except UnicodeDecodeError:
        readable = False
    return not readable and bool(_HINT.search(data))


def _redact(target: Path, plan: Plan) -> bool:
    """The hooks' secret scan + redaction on every written file. False if it could not run."""
    from corkern.commands import secrets_cmd
    try:
        plan.redacted, _ = secrets_cmd.redact_files(target, [str(target / rel) for rel in sorted(plan.files)])
    except (OSError, ValueError, guard.V1ProtectedError) as e:
        plan.unscanned = str(e) or type(e).__name__
        return False
    return True


PENDING = "migrate-pending"           # in .git/corkern: sha256 of the bundle, until the commit


def _digest(bundle: Path) -> str:
    return hashlib.sha256(guard.read_bytes(bundle)).hexdigest()


def unfinished(target: Path, bundle: Path) -> bool:
    """`target` holds this bundle's migration that stopped before its commit (scan failed)."""
    marker = repo_state_dir(target) / PENDING
    if not marker.is_file() or marker.is_symlink() or (target / ".git").is_symlink():
        return False
    try:
        guard.run_git(target, "rev-parse", "--verify", "-q", "HEAD")
        return False                                   # committed: not unfinished
    except guard.GitError:
        pass
    return (guard.read_shared(marker) or "").strip() == _digest(bundle)


def migrate(bundle: Path, dest: Path, dry_run: bool = False) -> Plan:
    target = guard.assert_safe(dest)
    require_dir_or_absent(target)
    resume = not guard.is_empty(target)
    if resume and not unfinished(target, bundle):
        raise InitError(f"{target} is not empty — migrate needs a new, empty destination.")
    plan = plan_from_bundle(bundle)
    # any text file (memories, registry, metrics) — a half-merged file must never become v2's start
    marked = [rel for rel, data in plan.files.items() if b"\0" not in data
              if any(line.startswith((b"<<<<<<< ", b">>>>>>> ")) for line in data.splitlines())]
    if marked:
        raise guard.BundleError(f"conflict markers in {', '.join(sorted(marked))} — resolve them in the source "
                                "(keep both sides) and export again")
    for rel in [rel for rel, data in plan.files.items() if unscannable(data)]:
        del plan.files[rel]
        plan.refused.append((rel, "could not be checked for secrets (binary or over 2 MB) — not imported"))
    if dry_run:
        return plan

    guard.set_knowledge_repo(target)              # nothing below it is written through a symlink
    if resume:
        extra = sorted(set(guard.run_git(target, "ls-files", "-o", "--exclude-standard", "-z").stdout.split("\0"))
                       - {""} - set(plan.files) - set(TEMPLATE))
        if extra:
            raise InitError(f"{target} holds files this migration did not write ({', '.join(extra[:5])}) — "
                            "move them away and run the same command again")
    for rel, content in TEMPLATE.items():
        if rel not in plan.files:
            guard.write_text(target / rel, content)
    for rel, data in plan.files.items():
        guard.write_bytes(target / rel, data)      # on a resume: the export's text again, then scanned
    guard.run_git(target, "init", "-q", "-b", "main")
    guard.write_text(repo_state_dir(target) / PENDING, _digest(bundle) + "\n")
    # the registry of this user's own export: confirmed on this machine (harvest, init-device)
    confirm_projects(target, _parse_conf(plan.files.get(CONF_FILE, b"").decode(errors="replace"), CONF_FILE))
    if not _redact(target, plan):
        return plan                 # fail closed: nothing committed unscanned
    stage(target, "-A")                           # never a per-machine file, whatever .gitignore says
    guard.run_git(target, "commit", "-q", "-m", MIGRATE_COMMIT_MSG)
    guard.remove_file(repo_state_dir(target) / PENDING)
    return plan
