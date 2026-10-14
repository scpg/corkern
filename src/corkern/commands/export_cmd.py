"""export / import — one verified file with everything corkern holds (format: corkern-export v1).

  corkern export [--out FILE]               knowledge repo (incl. not yet committed changes),
                                             this machine's local files, trust decisions and
                                             Claude Code wiring → one private file (0600)
  corkern import FILE [--check | --apply]   compare a v1 or v2 export with this knowledge repo;
                                             --apply imports what is missing, asking first

Never exported: private keys (each machine makes its own) and secrets removed before commit.
The import never overwrites: for a file that differs you choose keep mine (default), the
export's (the current one is kept as *.before-import-*), both (the export's is written next to
it), or — for MEMORY.md / .metrics.tsv — combine. Nothing is deleted; running it again is safe.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
from datetime import datetime
from pathlib import Path

import typer

from corkern import __version__, guard
from corkern.commands.reminders import fail
from corkern.commands.signing_cmd import device_name
from corkern.config import conf_entries, load_config, repo_format, valid_slug
from corkern.export_format import Export, ExportError, read, sha256, write

COMBINABLE = ("MEMORY.md", ".metrics.tsv")
# What an import may write into the knowledge repo: corkern's data, nothing else (a crafted
# export must not reach .git/, the template files or anything that runs). The IWE config can
# define commands, so it is imported only after its own yes, never with --yes.
IMPORTABLE = re.compile(r"^(memory/.+|\.devices/[^/]+|global/[^/]+|\.metrics\.tsv|\.iwe/config\.toml)$")
EXECUTABLE_CONFIG = {".iwe/config.toml"}


def _git(repo: Path, *args: str) -> str:
    try:
        return guard.run_git(repo, *args).stdout.strip()
    except guard.GitError:
        return ""


def _claude_wiring(projects: dict[str, Path], repo) -> tuple[dict[str, tuple[bytes, str]], list[str]]:
    members, notes = {}, []
    names = ["settings.json", "CLAUDE.md"] + [f"commands/{n}" for n in guard.list_claude_commands()]
    for name in names:
        try:
            text = guard.read_claude_config(name)
        except guard.V1ProtectedError:
            notes.append(f"{name}: belongs to v1 on this machine — not exported")
            continue
        if text is not None:
            members[f"claude/{name}"] = (text.encode(), "claude")
    wired = {}
    for slug, path in projects.items():
        settings = path / ".claude" / "settings.local.json"
        try:
            if settings.is_file():
                value = json.loads(guard.read_text(settings) or "{}").get("autoMemoryDirectory")
                if value:
                    wired[slug] = {"path": str(path), "autoMemoryDirectory": value}
        except (ValueError, guard.V1ProtectedError, AttributeError):
            continue
    if wired:
        members["claude/projects.json"] = ((json.dumps(wired, indent=2) + "\n").encode(), "claude")
    return members, notes


def export(
    out: Path = typer.Option(None, "--out", help="Export file (default ~/corkern-export-<device>-<time>.tar.gz)"),
) -> None:
    """Everything corkern holds on this machine → one verified, private file."""
    repo, projects = load_config()
    kr = repo.path
    me = device_name()
    out = out or Path.home() / f"corkern-export-{me}-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
    typer.echo(f"\ncorkern export — {me}, {kr}")
    members: dict[str, tuple[bytes, str]] = {}
    listed = _git(kr, "-c", "core.quotePath=false", "ls-files", "-co", "--exclude-standard").splitlines()
    for rel in listed:
        f = kr / rel
        if f.is_file() and not f.is_symlink():
            kind = "memory" if rel.startswith("memory/") else "devices" if rel.startswith(".devices/") else \
                "global" if rel.startswith("global/") else "registry" if rel == "corkern.conf" else \
                "metrics" if rel == ".metrics.tsv" else "repo"
            members[f"data/{rel}"] = (guard.read_bytes(f), kind)
    for rel in ("corkern.conf.local", ".git/corkern/trusted_signers", ".git/corkern/accepted"):
        if (kr / rel).is_file():
            members[f"local/{rel.removeprefix('.git/corkern/')}"] = (guard.read_bytes(kr / rel), "local")
    wiring, notes = _claude_wiring(projects, repo)
    members.update(wiring)
    uncommitted = [line[3:] for line in _git(kr, "status", "--porcelain").splitlines() if line]
    unpushed = _git(kr, "rev-list", "--count", "@{upstream}..HEAD")
    manifest = {
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tool": "corkern v2", "tool_version": __version__, "host": me,
        "user": os.environ.get("USER", ""), "home": str(Path.home()), "hostname": socket.gethostname(),
        "knowledge_repo_format": repo_format(kr),
        "repo": {"path": str(kr), "commit": _git(kr, "rev-parse", "HEAD"), "branch": _git(kr, "branch", "--show-current"),
                 "remote": _git(kr, "remote", "get-url", "origin"), "uncommitted": uncommitted,
                 "unpushed_commits": int(unpushed) if unpushed.isdigit() else None},
        "projects": [{"slug": s, "resolved": str(p)} for s, p in sorted(projects.items())],
    }
    try:
        result = write(out, manifest, members)
    except ExportError as e:
        fail(f"the written export does not verify: {e}")
    counts: dict[str, int] = {}
    for f in result.manifest["files"]:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    typer.echo(f"  ✓ {len(result.manifest['files'])} files: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    if uncommitted:
        typer.echo(f"  ! {len(uncommitted)} not yet committed change(s) included")
    for note in notes:
        typer.echo(f"  - {note}")
    typer.echo(f"  ✓ verified and written: {out} ({out.stat().st_size // 1024} KB, private: 0600)")
    typer.echo("    Not included by design: private keys (each machine makes its own), removed secrets.")
    typer.echo(f"\n  Check it any time:  corkern import {out} --check\n")


# ── import ──────────────────────────────────────────────────────────────────────────────
def _choose(question: str, yes: bool, default: str, with_yes: str) -> str:
    if yes:
        typer.echo(f"  {question} → {with_yes} (--yes)")
        return with_yes
    typer.echo(f"  {question} ", nl=False)
    answer = sys.stdin.readline().strip().lower() if not sys.stdin.closed else ""
    if not answer:
        typer.echo(f"→ {default}")
    return answer or default


def _inside(kr: Path, target: Path) -> bool:
    """target resolves inside the knowledge repo with no symlink on the way."""
    probe = target
    while probe != kr and probe != probe.parent:
        if probe.is_symlink():
            return False
        probe = probe.parent
    return str(target.resolve()).startswith(str(kr.resolve()) + os.sep)


def _side(path: Path, tag: str) -> Path:
    stem, dot, ext = path.name.rpartition(".")
    name = f"{stem}.{tag}.{ext}" if dot and stem else f"{path.name}.{tag}"
    return path.with_name(name)


def import_(
    file: Path = typer.Argument(..., help="A corkern export (from v1 or v2)"),
    check: bool = typer.Option(False, "--check", help="Compare only; change nothing (default)"),
    apply: bool = typer.Option(False, "--apply", help="Import what is missing — asks before each change"),
    yes: bool = typer.Option(False, "--yes", help="Accept the proposed choices (never overwrites)"),
    missing_only: bool = typer.Option(False, "--missing-only",
                                      help="With --apply: only import what is missing; leave differing files alone"),
) -> None:
    """Compare a corkern export with this knowledge repo; import what is missing (asks first)."""
    repo, _ = load_config()
    kr = repo.path
    try:
        exp = read(file)
    except ExportError as e:
        fail(f"{e}\n  Nothing was changed.")
    m = exp.manifest
    typer.echo(f"\ncorkern import — {file}")
    typer.echo(f"  {m.get('tool')} export from {exp.host}, {m.get('created')}, {len(m['files'])} files — all checksums match")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    registry_names = {"projects.conf", "corkern.conf"}
    identical, missing, different, ignored, config = [], [], [], [], []
    for rel, data in sorted(exp.files("data").items()):
        if rel in registry_names:
            continue
        target = kr / rel
        if not IMPORTABLE.match(rel) or not _inside(kr, target):
            ignored.append(rel)
        elif rel in EXECUTABLE_CONFIG and (not target.exists() or sha256(guard.read_bytes(target)) != sha256(data)):
            config.append(rel)
        elif not target.exists():
            missing.append(rel)
        elif sha256(guard.read_bytes(target)) == sha256(data):
            identical.append(rel)
        else:
            different.append(rel)
    have = {s for s, _ in conf_entries(guard.read_text(repo.conf_file))}
    new_projects = [line for line in exp.registry_text().splitlines()
                    if ":" in line and not line.lstrip().startswith("#")
                    and valid_slug(line.split(":", 1)[0].strip()) and line.split(":", 1)[0].strip() not in have]
    exported = set(exp.files("data"))
    only_here = [rel for rel in _git(kr, "-c", "core.quotePath=false", "ls-files").splitlines()
                 if rel.startswith(("memory/", "global/", ".devices/")) and rel not in exported]

    typer.echo("\ncorkern data")
    typer.echo(f"  ✓ {len(identical)} identical")
    for label, group in (("missing here", missing), ("different here", different)):
        if group:
            typer.echo(f"  ! {len(group)} {label}:")
            for rel in group[:15]:
                typer.echo(f"      {rel}")
            if len(group) > 15:
                typer.echo(f"      … and {len(group) - 15} more")
    if new_projects:
        typer.echo(f"  ! {len(new_projects)} project registration(s) not here: "
                   + ", ".join(line.split(':', 1)[0] for line in new_projects))
    if only_here:
        typer.echo(f"    {len(only_here)} file(s) only here — left alone")
    template = {".gitattributes", ".gitignore", "README.md", ".corkern-format", ".corkern-migration"}
    for rel in ignored:
        if rel not in template:
            typer.echo(f"  ! {rel}: not corkern data (or behind a symlink) — never imported")
    for rel in config:
        typer.echo(f"  ! {rel}: can define commands — imported only after its own yes, never with --yes")
    typer.echo("\nNot imported (machine-specific)")
    claude = exp.files("claude")
    if claude:
        typer.echo(f"    Claude Code wiring from {exp.host} ({len(claude)} item(s)) — this machine sets up its own: "
                   "corkern init-device")
    local = exp.files("local")
    if local:
        typer.echo(f"    {', '.join(sorted(local))} — {exp.host}'s own paths/trust decisions "
                   "(trust devices here with corkern trust)")
    transcripts = exp.files("transcripts")
    if transcripts:
        typer.echo(f"    {len(transcripts)} transcript(s) — stay in the export file")
    if not apply:
        typer.echo(f"\n  --check: nothing was changed. To import with questions: corkern import {file} --apply\n")
        return

    data = exp.files("data")
    done = {"imported": 0, "combined": 0, "both": 0, "replaced": 0, "kept": 0, "projects": 0}
    if missing and _choose(f"Import the {len(missing)} missing file(s)? [y/N]", yes, "n", "y") == "y":
        for rel in missing:
            if _inside(kr, kr / rel):
                guard.write_bytes(kr / rel, data[rel])
            done["imported"] += 1
    for rel in ([] if missing_only else different):
        target = kr / rel
        combinable = target.name in COMBINABLE
        options = "[k]eep mine / [e]xport's / [b]oth" + (" / [c]ombine lines" if combinable else "")
        choice = _choose(f"{rel} differs — {options} (default k)?", yes, "k", "c" if combinable else "b")
        if choice == "e":
            guard.write_bytes(_side(target, f"before-import-{stamp}"), guard.read_bytes(target))
            guard.write_bytes(target, data[rel])
            done["replaced"] += 1
        elif choice == "b":
            guard.write_bytes(_side(target, f"from-export-{exp.host}-{stamp}"), data[rel])
            done["both"] += 1
        elif choice == "c" and combinable:
            mine = guard.read_text(target)
            lines = set(mine.splitlines())
            extra = [line for line in data[rel].decode(errors="replace").splitlines() if line not in lines]
            if extra:
                guard.write_text(target, mine + ("" if not mine or mine.endswith("\n") else "\n") + "\n".join(extra) + "\n")
            done["combined"] += 1
        else:
            done["kept"] += 1
    for rel in config:
        if not yes and _choose(f"{rel} can make IWE run commands. Import it (only if you made this export)? [y/N]",
                               False, "n", "n") == "y":
            target = kr / rel
            if target.exists():
                guard.write_bytes(_side(target, f"before-import-{stamp}"), guard.read_bytes(target))
            guard.write_bytes(target, data[rel])
            done["imported"] += 1
    if new_projects and _choose(f"Register the {len(new_projects)} project(s) here? [y/N]", yes, "n", "y") == "y":
        text = guard.read_text(repo.conf_file)
        guard.write_text(repo.conf_file, text + ("" if not text or text.endswith("\n") else "\n") + "\n".join(new_projects) + "\n")
        done["projects"] = len(new_projects)
    if missing_only and different:
        typer.echo(f"  {len(different)} differing file(s) left as they are (--missing-only)")
    typer.echo("\nDone")
    typer.echo(f"  ✓ {len(identical)} identical, {done['imported']} imported, {done['combined']} combined, "
               f"{done['both']} kept both, {done['replaced']} replaced (old copy kept), {done['kept']} kept as they were, "
               f"{done['projects']} project(s) registered")
    typer.echo("  Nothing was deleted. The next hook run commits and syncs it.\n")
