"""status / doctor — health of the knowledge repo, sync, and tools (v1 `status` parity).

Exit 1 only for states that need action (not a git repo, stuck mid-rebase/merge); everything
else is a warning. `~/.claude` is not inspected: v2 never touches it before UAT.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import typer

from corkern import __version__, guard
from corkern.commands.init_device_cmd import HOOK_EVENTS
from corkern.commands.secrets_cmd import log_entries
from corkern.commands.trust_cmd import HELD, trust_file
from corkern.config import load_config, state_dir
from corkern.memory_index import usage


class Report:
    def __init__(self) -> None:
        self.failed = False
        self.problems = 0
        self.warnings = 0

    def ok(self, msg: str) -> None:
        typer.echo(f"  ✓ {msg}")

    def warn(self, msg: str) -> None:
        typer.echo(f"  ! {msg}")
        self.warnings += 1

    def fail(self, msg: str) -> None:
        typer.echo(f"  ✗ {msg}")
        self.failed = True
        self.problems += 1

    def info(self, msg: str) -> None:
        typer.echo(f"    {msg}")

    def header(self, title: str) -> None:
        typer.echo(f"\n{title}")


def _git(repo: Path, *args: str, timeout: float = 10) -> str | None:
    try:
        return guard.run_git(repo, *args, timeout=timeout).stdout.strip()
    except guard.GitError:
        return None


def _read(path: Path) -> str | None:
    return guard.read_text(path).strip() if path.is_file() else None


def _sync_checks(r: Report, kr: Path) -> None:
    r.header("Sync")
    if not (kr / ".git").exists():
        r.fail(f"Not a git repo — run: git -C {kr} init")
        return
    if (kr / ".git/rebase-merge").exists() or (kr / ".git/rebase-apply").exists() \
            or (kr / ".git/MERGE_HEAD").exists():
        r.fail(f"Repo is stuck in a rebase/merge — resolve it in {kr}")
    remote = _git(kr, "remote", "get-url", "origin")
    if not remote:
        r.warn("No remote configured — memories stay on this machine "
               f"(git -C {kr} remote add origin <url>)")
    else:
        r.ok(f"Remote: {remote}")
        if _git(kr, "ls-remote", "--quiet", "origin", "HEAD", timeout=10) is None:
            r.warn("Remote not reachable (offline or auth issue)")
        counts = _git(kr, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
        if counts:
            behind, ahead = (int(x) for x in counts.split())
            if ahead:
                r.warn(f"{ahead} commit(s) not pushed yet (pushed at session end)")
            if behind:
                r.warn(f"{behind} commit(s) not pulled yet (pulled at session start)")
            if not ahead and not behind:
                r.ok("In sync with the remote (as of the last fetch)")
    dirty = _git(kr, "status", "--porcelain")
    if dirty:
        r.warn(f"{len(dirty.splitlines())} uncommitted change(s) — committed by the next hook run")
    for what in ("pull", "push"):
        stamp, error = _read(kr / f".last-sync-{what}"), _read(kr / f".last-sync-{what}-error")
        if error:
            reason = next((line.split(":", 1)[1].strip() for line in error.splitlines()
                           if line.startswith("reason")), "see file")
            r.fail(f"Last {what} failed: {reason}  ({kr / f'.last-sync-{what}-error'})") \
                if what == "push" else r.warn(f"Last {what} failed: {reason}")
        elif stamp:
            r.ok(f"Last {what}: {stamp}")
        else:
            r.warn(f"Last {what}: never recorded (hooks not wired on this machine yet)")
    conflicts = _read(kr / ".last-sync-conflicts")
    if conflicts:
        r.warn(f"{len(conflicts.splitlines())} conflicting edit(s) kept side by side as "
               "*.conflict-<host>-<time>.* — review and merge them by hand")
    problem = _read(state_dir() / "last-hook-problem")
    if problem:
        r.warn(f"Last hook problem: {problem}")


def _repo_checks(r: Report, kr: Path) -> None:
    r.header("Knowledge repo")
    r.ok(f"Path: {kr}")
    from corkern.config import repo_format
    from corkern.knowledge_repo import FORMAT_VERSION
    fmt = repo_format(kr)
    if fmt is None:
        r.warn(f"No format marker (.corkern-format) — add it: echo knowledge_repo_format={FORMAT_VERSION} "
               f"> {kr}/.corkern-format")
    else:
        r.ok(f"Format: knowledge repo format {fmt}")
    ignore = _read(kr / ".gitignore") or ""
    for pattern in ("corkern.conf.local", ".last-sync-*", ".corkern.lock", ".distill-prompt.md"):
        if pattern not in ignore:
            r.warn(f"{pattern} not in .gitignore — machine-local state could be committed")
    attributes = _read(kr / ".gitattributes") or ""
    for pattern in ("memory/**/MEMORY.md", "corkern.conf", ".metrics.tsv"):
        if f"{pattern} merge=union" not in attributes:
            r.warn(f"No union merge for {pattern} — concurrent edits on two devices become "
                   f"conflict copies (add to .gitattributes: {pattern} merge=union)")
    sign = _git(kr, "config", "--get", "commit.gpgsign")
    program = _git(kr, "config", "--get", "gpg.ssh.program") or ""
    local_key = _git(kr, "config", "--local", "--get", "user.signingKey")
    if local_key:
        if Path(local_key).is_file():
            r.ok(f"Commits signed with this machine's key ({local_key})")
        else:
            r.fail(f"Signing key {local_key} is missing — run: corkern signing")
    elif sign == "true" and "op-ssh-sign" in program:
        r.warn("Commits are signed through 1Password (interactive) — hook commits will "
               "wait for biometrics or fail; run: corkern signing")


CREDENTIAL_PATTERNS = (".env*", "*.pem", "*.key", "id_rsa", "id_ed25519")


def _safety_checks(r: Report, repo) -> None:
    kr = repo.path
    r.header("Secrets and trust")
    ignore = _read(kr / ".gitignore") or ""
    missing = [p for p in CREDENTIAL_PATTERNS if p not in ignore.split()]
    if missing:
        r.warn(f"Credential files not ignored by git: {' '.join(missing)} — add them to "
               f"{kr / '.gitignore'} (the secret scan still redacts their content)")
    removed = log_entries()
    if removed:
        r.warn(f"{len(removed)} secret(s) were removed from memories before commit; the originals "
               "are kept on this machine only — review: corkern secrets")
    else:
        r.ok("No secrets removed before commit on this machine (check the repo: corkern secrets --scan)")
    held = _read(kr / HELD)
    if trust_file(kr).is_file():
        trusted = [line for line in guard.read_text(trust_file(kr)).splitlines() if line.strip()]
        r.ok(f"Pull verification on — {len(trusted)} trusted device key(s)")
        if held:
            r.warn(f"{len(held.split())} incoming commit(s) held back: not signed by a device this "
                   "machine trusts — review: corkern trust")
    elif _git(kr, "config", "--local", "--get", "user.signingKey"):
        r.warn("Pull verification is off — any device that can push can change these memories. "
               "Turn it on: corkern trust --enable")
    else:
        r.info("Pull verification needs a signing key first: corkern signing, then corkern trust --enable")


def _index_checks(r: Report, repo) -> None:
    r.header("Memory indexes (Claude Code loads the first 200 lines / 25 KB of MEMORY.md)")
    checked = 0
    for slug in repo.slugs():
        index = repo.memory_dir / slug / "MEMORY.md"
        if not index.is_file():
            continue
        checked += 1
        u = usage(guard.read_text(index))
        if u.over:
            r.warn(f"{slug}: {u.describe()} — entries past the limit are NOT loaded. Shorten it: one "
                   "line per entry, details in topic files (corkern memory-prune / distill help)")
        elif u.near:
            r.warn(f"{slug}: {u.describe()} — close to the limit")
    if checked:
        r.ok(f"{checked} index(es) checked")


def _tools(r: Report) -> None:
    r.header("Tools")
    for tool, why in (("git", "required"), ("flock", "serialises hooks"),
                      ("timeout", "bounds network checks in hooks")):
        (r.ok if guard.which(tool) else r.fail)(f"{tool} ({why})")
    if guard.which("graphify"):
        r.ok("graphify (knowledge graphs)")
    else:
        r.warn("graphify not installed — optional (uv tool install graphifyy)")
    _hook_wiring(r)


def _hook_wiring(r: Report) -> None:
    r.header("Claude Code wiring")
    try:
        text = guard.read_claude_config("settings.json")
    except guard.V1ProtectedError:
        r.info("Claude Code's settings belong to corkern v1 on this machine — v2 wires them after the "
               "switch (corkern init-device)")
        return
    try:
        hooks = (json.loads(text) if text else {}).get("hooks", {})
    except (json.JSONDecodeError, AttributeError):
        r.warn(f"{guard.claude_config_dir() / 'settings.json'} is not valid JSON")
        return
    missing = [e for e in HOOK_EVENTS
               if not any("corkern.sh" in str(h.get("command", "")) for g in hooks.get(e, []) for h in g.get("hooks", []))]
    if missing:
        r.warn(f"corkern hooks missing for {', '.join(missing)} — memories are not synced "
               "automatically. Run: corkern init-device --apply")
    else:
        r.ok("Hooks wired in Claude Code (session start, memory writes, reply end)")


GRAPH_STALE_DAYS = 30


def _verbose(r: Report, repo, projects: dict[str, Path]) -> None:
    from corkern.commands import init_device_cmd, iwe_cmd
    from corkern.commands.list_cmd import local_status

    r.header("Project wiring (Claude Code memory → knowledge repo)")
    for slug, path in sorted(projects.items()):
        state = local_status(path, repo.memory_dir / slug)
        if state == "synced":
            r.ok(slug)
        elif state == "not wired":
            r.warn(f"{slug}: Claude Code still uses its own memory folder — run: corkern init-device --apply")
        elif state == "other dir":
            r.warn(f"{slug}: autoMemoryDirectory points somewhere else — run: corkern init-device --apply")
        elif state == "no path":
            r.info(f"- {slug}: not on this machine ({path})")
        else:
            r.info(f"- {slug}: {state} ({path})")

    r.header("Hook scripts and CLI")
    from corkern import __version__, hooks_install
    where = hooks_install.home() / "hooks"
    hooks_state = hooks_install.state()
    if hooks_state == "current":
        r.ok(f"hook scripts in {where} (corkern {__version__})")
    elif hooks_state == "outdated":
        r.warn(f"hook scripts in {where} are from corkern {hooks_install.installed_version()}, "
               f"this is {__version__} — run: corkern init-device --apply")
    elif hooks_state == "git-clone":
        r.warn(f"{hooks_install.home()} is an older git-clone install of corkern — hooks run from it; "
               f"to switch: mv {hooks_install.home()} {hooks_install.home()}.old-clone && corkern init-device --apply")
    else:
        r.fail(f"hook scripts missing in {where} — run: corkern init-device --apply")
    cli = guard.which("corkern")
    (r.ok if cli else r.warn)(f"corkern on PATH ({cli})" if cli else "corkern is not on PATH — hooks fall back to ~/.local/bin/corkern")
    present = guard.list_claude_commands()
    folder = hooks_install.packaged("claude_commands")
    expected = sorted(name for name, _ in guard.list_files(folder, ".md")
                      if name.startswith("corkern-")) if folder.is_dir() else []
    missing = [c for c in expected if c not in present]
    if missing:
        r.warn(f"slash commands missing: {' '.join('/' + c[:-3] for c in missing)} — run: corkern init-device --apply")
    elif expected:
        r.ok(f"slash commands: {' '.join('/' + c[:-3] for c in expected)}")

    r.header("Claude Code memory folders not in the knowledge repo")
    wired = {guard.claude_project_key(p) for s, p in projects.items()
             if local_status(p, repo.memory_dir / s) == "synced"}
    found = 0
    for key, count, is_link in guard.claude_memory_dirs():
        if is_link or count == 0:
            continue
        found += 1
        note = " (old notes from before init-device)" if key in wired else ""
        r.warn(f"{key}: {count} file(s) in Claude Code's own memory folder, not synced{note} — "
               "copy what you need into memory/<slug>/ of the knowledge repo")
    if not found:
        r.ok("none — every Claude Code memory folder in use is the knowledge repo")

    r.header("Knowledge graphs (graphify)")
    stale, without = [], []
    for slug, path in sorted(projects.items()):
        graph = path / "graphify-out" / "graph.json"
        if graph.is_file():
            age = int((datetime.now().timestamp() - graph.stat().st_mtime) // 86400)
            when = datetime.fromtimestamp(graph.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            if age >= GRAPH_STALE_DAYS:
                r.warn(f"{slug} — graph built {when} ({age}d ago) — rebuild: corkern graphify {slug}")
                stale.append(slug)
            else:
                r.ok(f"{slug} — graph built {when} ({age}d ago)")
        elif path.is_dir() and path != Path.home():
            without.append(slug)
    if without:
        r.warn(f"{len(without)} project(s) without a knowledge graph: {' '.join(without)} — "
               "corkern graphify <slug>, then corkern graphify <slug> --install")

    r.header("IWE knowledge graph (MCP)")
    iwe_bin, iwec_bin = iwe_cmd._tool("iwe"), iwe_cmd._tool("iwec")
    if not (iwe_bin and iwec_bin):
        r.info(f"IWE not installed (optional) — {iwe_cmd.INSTALL_HINT}")
    elif not (repo.path / iwe_cmd.CONFIG).is_file():
        r.warn("IWE installed but not set up for the knowledge repo — run: corkern iwe --setup")
    else:
        registered = iwe_cmd._registered(repo.path) or ""
        if "iwe-serve" in registered:
            r.ok("IWE configured and registered as MCP server 'iwe'")
        elif registered and guard.v1_installed():
            r.info("IWE configured; Claude Code's 'iwe' server is v1's until the switch")
        else:
            r.warn("IWE configured but not registered in Claude Code — run: corkern iwe --install")


def _installation(r: Report) -> None:
    """Which corkern copies, v1 leftovers and old installs exist on this machine."""
    from corkern import instance
    inv = instance.inventory()
    r.header("Installation (one corkern per machine)")
    r.info(f"this copy: corkern {__version__} — {instance.describe(inv.this)} ({inv.this.path})")
    for c in inv.copies:
        r.info(f"on PATH : {c.path} — {instance.describe(c)}" + (f" ({c.note})" if c.note else ""))
    for line in inv.v1:
        r.info(f"v1      : {line}")
    for msg in inv.problems:
        r.fail(msg)
    for msg in inv.warnings:
        r.warn(msg)
    if not inv.problems and not inv.warnings:
        r.ok("exactly one corkern, installed as a tool; no v1, no old install left")


def doctor() -> None:
    """Check this machine's corkern installation (works before setup, never changes anything)."""
    from corkern.config import memory_dir_setting
    if memory_dir_setting():
        status(verbose=True)
        return
    r = Report()
    _installation(r)
    r.header("Knowledge repo")
    r.warn("none set for this machine yet — run: corkern setup --clone <url> (or --memory-dir PATH)")
    typer.echo("\n" + "─" * 32)
    typer.echo(f"  {r.problems} problem(s), {r.warnings} warning(s) — the lines above say how to fix them.")
    if r.failed:
        raise typer.Exit(1)


def status(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Full diagnostics (v1: status -v)"),
) -> None:
    """Health check: knowledge repo, sync state, tools, projects (-v: full diagnostics)."""
    repo, projects = load_config()
    r = Report()
    typer.echo(f"Memory repo : {repo.path}")
    typer.echo(f"Projects    : {len(projects)}")
    _installation(r)
    _repo_checks(r, repo.path)
    _sync_checks(r, repo.path)
    _safety_checks(r, repo)
    _index_checks(r, repo)
    _tools(r)
    if verbose:
        _verbose(r, repo, projects)
    r.header("Projects")
    for slug, path in sorted(projects.items()):
        marker = "✓" if path.exists() else "✗"
        typer.echo(f"  {marker}  {slug:<20} {path}")
    typer.echo("\n" + "─" * 32)
    if not r.problems and not r.warnings:
        typer.echo("  All good." + ("" if verbose else "  Full diagnostics: corkern status -v"))
    else:
        typer.echo(f"  {r.problems} problem(s), {r.warnings} warning(s) — the lines above say how to fix them."
                   + ("" if verbose else "  Full diagnostics: corkern status -v"))
    if r.failed:
        raise typer.Exit(1)
