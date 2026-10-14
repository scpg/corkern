"""v1 isolation guard — the single choke point for all v2 filesystem access.

Every path v2 reads, writes, or lists goes through this module; a structural test
(tests/test_guard_enforcement.py) fails the build if any other module touches the
filesystem directly.

A path is refused if the path itself or any ancestor — checked both as given and
with symlinks resolved — matches one of:

  1. contains ``.corkern-v1.sentinel``          (physical tripwire)
  2. contains ``projects.conf``                 (v1 registry; v2 uses corkern.conf)
  3. is a known v1 location: ``~/dev/corkern``, ``$CORKERN``
  4. is v1 runtime state: ``~/.claude/`` (settings, hooks, memory symlinks),
     ``~/.local/bin/corkern`` while it is v1's (a link into a repo with ``projects.conf``)

"~" is checked for both $HOME and the account's home from the password database,
so overriding HOME cannot hide the real v1 repo. Loosening any rule is a deliberate
code change, to be made only after v2 UAT.

Inside a knowledge repo (registered by config.MemoryRepo), a path is also refused when any
component below the repo root is a symlink, or when it resolves outside the root
(SymlinkInRepoError): the repo is shared, and a synced symlink can point at any file on this
machine. The root itself may be a symlink. Listing skips such links, read_shared treats them as
absent, and writes open with O_NOFOLLOW, so a link swapped in after the check is not followed.

Two narrow exceptions:
- `init-device`: Claude Code's user `settings.json`, `CLAUDE.md` and corkern's own slash
  commands (`commands/corkern-*.md`) in $CLAUDE_CONFIG_DIR or ~/.claude may be read and written
  through read_claude_config / write_claude_config — never through a symlink, never when v1 is
  installed for that home, nothing else in that directory.
- `harvest` / `prune` / `status -v`: Claude Code's conversation transcripts
  (`projects/<key>/*.jsonl`) and its project memory folders may be LISTED and READ — never
  written (list_transcripts / read_transcript / claude_memory_dirs).
"""
from __future__ import annotations

import errno
import os
import pwd
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

_SENTINEL = ".corkern-v1.sentinel"
_V1_REGISTRY = "projects.conf"


class V1ProtectedError(SystemExit):
    """Hard stop. Subclasses SystemExit so `except Exception` cannot swallow it."""


class SymlinkInRepoError(V1ProtectedError):
    """A path inside the knowledge repo goes through a symlink (never followed)."""


class GitError(RuntimeError):
    pass


class BundleError(ValueError):
    pass


_MAX_BUNDLE_BYTES = 500 * 1024 * 1024


def _homes() -> set[Path]:
    homes = {Path.home()}
    try:
        homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir))
    except KeyError:
        pass
    return homes


def _protected_roots() -> set[Path]:
    roots: set[Path] = set()
    for home in _homes():
        roots |= {home / "dev" / "corkern", home / ".claude"}
        if is_v1_command(home / ".local" / "bin" / "corkern"):
            roots.add(home / ".local" / "bin" / "corkern")
    if os.environ.get("CORKERN"):
        roots.add(Path(os.environ["CORKERN"]).expanduser())
    # Compare against both spellings of each root (e.g. /home vs a symlinked home)
    return {Path(os.path.abspath(r)) for r in roots} | {r.resolve() for r in roots}


def _refusal_reason(path: Path, roots: set[Path]) -> str | None:
    for candidate in (path, *path.parents):
        if candidate in roots:
            return f"{candidate} is a known v1 location"
        if (candidate / _SENTINEL).exists():
            return f"{candidate} contains {_SENTINEL}"
        if (candidate / _V1_REGISTRY).exists():
            return f"{candidate} contains {_V1_REGISTRY} (v1 layout)"
    return None


# Knowledge repo roots: (path as given, resolved). Paths below them never go through a symlink.
_repo_roots: set[tuple[Path, Path]] = set()


def set_knowledge_repo(path: Path | str) -> None:
    raw = Path(os.path.abspath(Path(path).expanduser()))
    _repo_roots.add((raw, raw.resolve()))


def _repo_symlink(raw: Path, resolved: Path) -> str | None:
    """Why `raw` may not be used: it goes through a symlink below a knowledge repo root, or
    resolves outside it. None for paths outside every knowledge repo."""
    for spelled, root in _repo_roots:
        base = next((b for b in (spelled, root) if raw != b and raw.is_relative_to(b)), None)
        if base is not None:
            probe, rest = base, raw.relative_to(base).parts
        elif resolved != root and resolved.is_relative_to(root):
            # reached through a link outside the repo (e.g. ~/.corkern-link/memory): find where
            # the path enters the repo, then check every component after that
            probe, rest = Path(raw.anchor), raw.parts[1:]
            while rest and not (probe.resolve() == root or probe.resolve().is_relative_to(root)):
                probe, rest = probe / rest[0], rest[1:]
        else:
            continue
        for part in rest:
            probe = probe / part
            if probe.is_symlink():
                return f"{probe} is a symlink inside the knowledge repo {root}"
        if not resolved.is_relative_to(root):
            return f"it resolves outside the knowledge repo {root}"
    return None


def symlink_reason(path: Path | str) -> str | None:
    """Why `path` would be refused as going through a symlink in the knowledge repo, if it would."""
    raw = Path(os.path.abspath(Path(path).expanduser()))
    return _repo_symlink(raw, raw.resolve())


def assert_safe(path: Path | str) -> Path:
    """Return the resolved path, or raise V1ProtectedError if it touches v1
    (SymlinkInRepoError if it goes through a symlink inside the knowledge repo)."""
    raw = Path(os.path.abspath(Path(path).expanduser()))
    resolved = raw.resolve()
    roots = _protected_roots()
    for form in (raw, resolved):
        reason = _refusal_reason(form, roots)
        if reason:
            raise V1ProtectedError(
                f"ERROR: {path} is v1-protected ({reason}).\n"
                "Set CORKERN_MEMORY_DIR to a test knowledge repo and retry."
            )
    reason = _repo_symlink(raw, resolved)
    if reason:
        raise SymlinkInRepoError(
            f"ERROR: {path} not used: {reason}.\n"
            "corkern never follows symlinks in the knowledge repo — a synced link can point at any "
            "file on this machine. Replace it with a regular file or remove it (corkern status lists them).")
    return resolved


# settings.local.json: only as the "home" project's local settings (a project at ~)
CLAUDE_FILES = ("settings.json", "CLAUDE.md", "settings.local.json")


def is_v1_command(path: Path) -> bool:
    """v1 installs `corkern` as a link into its repo (which holds projects.conf); v2's `corkern`
    is uv's / pipx's own command."""
    return path.is_symlink() and (path.resolve().parent / _V1_REGISTRY).exists()


def v1_installed_for(home: Path) -> bool:
    """v1 is (or was) set up for this home: its repo, its command or its hook dispatcher."""
    return any(p.exists() or p.is_symlink() for p in (
        home / "dev" / "corkern" / _SENTINEL, home / "dev" / "corkern" / _V1_REGISTRY,
        home / ".claude" / "corkern.sh")) or is_v1_command(home / ".local" / "bin" / "corkern")


def v1_installed() -> bool:
    return bool(os.environ.get("CORKERN")) or any(v1_installed_for(h) for h in _homes())


def claude_config_dir() -> Path:
    return Path(os.path.abspath(Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()))


def _claude_target(name: str) -> Path:
    import re
    base = name.split(".bak-corkern-")[0]
    if not (base in CLAUDE_FILES or re.fullmatch(r"commands/corkern-[a-z0-9-]+\.md", base)) \
            or ".." in name or name.startswith("/"):
        raise ValueError(f"{name} is not a Claude Code file corkern may touch")
    target = claude_config_dir() / name
    if target.is_symlink():
        raise V1ProtectedError(f"ERROR: {target} is a symlink (v1 links it into its repo) — not touched.")
    resolved = target.resolve()
    for home in _homes():
        for root in {Path(os.path.abspath(home / ".claude")), (home / ".claude").resolve()}:
            inside = any(f == root or root in f.parents for f in (target, resolved))
            if inside and v1_installed_for(home):
                raise V1ProtectedError(
                    f"ERROR: {target} belongs to corkern v1 on this machine (v1 is installed for {home}).\n"
                    "Claude Code's settings switch to v2 only after UAT.")
    other_roots = _protected_roots() - {Path(os.path.abspath(h / ".claude")) for h in _homes()} \
        - {(h / ".claude").resolve() for h in _homes()}
    for form in (target, resolved):
        reason = _refusal_reason(form, other_roots)
        if reason:
            raise V1ProtectedError(f"ERROR: {target} is v1-protected ({reason}).")
    return resolved


def claude_project_key(project: Path) -> str:
    """Claude Code's folder name for a project: every non-alphanumeric character becomes '-'."""
    import re
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project))


def _transcript_dir(project: Path) -> Path:
    return claude_config_dir() / "projects" / claude_project_key(project)


def list_transcripts(project: Path) -> list[Path]:
    """The project's conversation transcripts (*.jsonl), newest first. Read-only access."""
    folder = _transcript_dir(project)
    if not folder.is_dir():
        return []
    files = [f for f in folder.iterdir() if f.suffix == ".jsonl" and f.is_file() and not f.is_symlink()]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def transcript_dir(project: Path) -> Path:
    return _transcript_dir(project)


def read_transcript(path: Path) -> bytes:
    root = (claude_config_dir() / "projects").resolve()
    target = Path(path).resolve()
    if target.suffix != ".jsonl" or root not in target.parents or Path(path).is_symlink():
        raise ValueError(f"{path} is not a Claude Code transcript")
    return target.read_bytes()


def claude_memory_dirs() -> list[tuple[str, int, bool]]:
    """(project key, number of files, is a symlink) for every projects/<key>/memory. Read-only."""
    root = claude_config_dir() / "projects"
    if not root.is_dir():
        return []
    out = []
    for project in sorted(root.iterdir()):
        mem = project / "memory"
        if mem.is_symlink():
            out.append((project.name, 0, True))
        elif mem.is_dir():
            out.append((project.name, sum(1 for f in mem.iterdir() if f.is_file()), False))
    return out


def read_claude_config(name: str) -> str | None:
    target = _claude_target(name)
    return target.read_text() if target.is_file() else None


def write_claude_config(name: str, content: str) -> Path | None:
    """Write settings.json / CLAUDE.md; an existing file is first copied to
    <name>.bak-corkern-<time>. Returns the backup path, if one was made."""
    target = _claude_target(name)
    backup = None
    if target.is_file():
        from datetime import datetime
        backup = _claude_target(f"{name}.bak-corkern-{datetime.now():%Y%m%d-%H%M%S}")
        backup.write_text(target.read_text())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return backup


def list_claude_commands() -> list[str]:
    """corkern's slash commands present in Claude Code's commands folder."""
    folder = claude_config_dir() / "commands"
    if not folder.is_dir():
        return []
    return sorted(f.name for f in folder.iterdir() if f.name.startswith("corkern-") and f.suffix == ".md")


_WRITE = os.O_WRONLY | os.O_CREAT | os.O_TRUNC


def _open(target: Path, flags: int, mode: str, perms: int = 0o666):
    """open() that never follows a symlink as the last component (ELOOP instead) — closes the
    gap between assert_safe and the open."""
    return os.fdopen(os.open(target, flags | os.O_NOFOLLOW, perms), mode)


def read_text(path: Path) -> str:
    with _open(assert_safe(path), os.O_RDONLY, "r") as f:
        return f.read()


def read_shared(path: Path) -> str | None:
    """A synced file's text, or None when it is missing, not a regular file, or reached through a
    symlink in the knowledge repo — such a link is treated as absent, never followed and never an
    error (one teammate's link must not break everyone's corkern)."""
    try:
        target = assert_safe(path)
        if not target.is_file():
            return None
        with _open(target, os.O_RDONLY, "r") as f:
            return f.read()
    except SymlinkInRepoError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:          # swapped for a link after the check
            return None
        raise


def write_text(path: Path, content: str) -> None:
    target = assert_safe(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _open(target, _WRITE, "w") as f:
        f.write(content)


def write_private(path: Path, content: str) -> None:
    """Write a file only this user can read (0600, new parent dirs 0700) — for secrets kept
    out of the knowledge repo."""
    target = assert_safe(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _open(target, _WRITE, "w", 0o600) as f:
        f.write(content)
    os.chmod(target, 0o600)


def remove_symlink(path: Path) -> None:
    """Remove a symlink inside the knowledge repo — the link only, never what it points at —
    so a regular file can take its place. Anything that is not such a link is left alone."""
    raw = Path(os.path.abspath(Path(path).expanduser()))
    assert_safe(raw.parent)                  # no symlink on the way to the link itself
    inside = any(raw.parent.resolve().is_relative_to(root) for _, root in _repo_roots)
    if inside and raw.is_symlink():
        raw.unlink()


def remove_file(path: Path) -> None:
    """Delete one regular file (never a directory); missing is fine."""
    target = assert_safe(path)
    if target.is_file():
        target.unlink()


def write_bytes(path: Path, content: bytes) -> None:
    target = assert_safe(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _open(target, _WRITE, "wb") as f:
        f.write(content)


def read_tar(path: Path) -> tuple[dict[str, bytes], list[str]]:
    """Read a tarball into memory without extracting it to disk.

    Returns ({relative posix path: content} for regular files, [names of skipped
    non-file entries such as symlinks]). Any absolute or `..` entry rejects the whole
    bundle, so a crafted archive cannot steer writes outside the destination.
    """
    target = assert_safe(path)
    if not target.is_file():
        raise BundleError(f"{target} not found.")
    try:
        tar = tarfile.open(target)
    except tarfile.TarError:
        raise BundleError(f"{target} is not a valid tar archive.")

    files: dict[str, bytes] = {}
    skipped: list[str] = []
    total = 0
    with tar:
        for member in tar:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise BundleError(f"unsafe entry in bundle: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():
                skipped.append(name.as_posix())
                continue
            total += member.size
            if total > _MAX_BUNDLE_BYTES:
                raise BundleError(f"bundle exceeds {_MAX_BUNDLE_BYTES // 2**20} MB.")
            files[name.as_posix()] = tar.extractfile(member).read()
    return files, skipped


def write_tar_private(path: Path, members: list[tuple[str, bytes]]) -> None:
    """Write a gzip'd tar only this user can read (0600) with the given (name, content) members."""
    import io
    import time
    target = assert_safe(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
            tar.addfile(info, io.BytesIO(data))
    os.chmod(target, 0o600)


def append_text(path: Path, content: str) -> None:
    target = assert_safe(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, "a") as f:
        f.write(content)


def _listable(path: Path) -> Path | None:
    """The folder to list, or None when it is missing or reached through a symlink in the
    knowledge repo (listing treats such a link as absent)."""
    try:
        target = assert_safe(path)
    except SymlinkInRepoError:
        return None
    return target if target.is_dir() else None


def list_files(path: Path, suffix: str = "") -> list[tuple[str, float]]:
    """(name, mtime) of non-hidden regular files ending in `suffix`, newest first. Symlinks are
    skipped, never followed."""
    target = _listable(path)
    if target is None:
        return []
    files = [(f.name, f.stat().st_mtime) for f in target.iterdir()
             if not f.is_symlink() and f.is_file() and not f.name.startswith(".") and f.name.endswith(suffix)]
    return sorted(files, key=lambda item: item[1], reverse=True)


def is_empty(path: Path) -> bool:
    """True if the path does not exist or is a directory with no entries (hidden included)."""
    target = assert_safe(path)
    if not target.exists():
        return True
    return target.is_dir() and not any(target.iterdir())


def run_git(repo: Path, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run git inside `repo` (git -C). The only subprocess v2 is allowed to start."""
    target = assert_safe(repo)
    try:
        result = subprocess.run(["git", "-C", str(target), *args], capture_output=True, text=True,
                                timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args)} timed out after {timeout}s in {target}")
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {target}:\n{result.stderr.strip()}")
    return result


def list_dirs(path: Path) -> list[str]:
    """Names of non-hidden subdirectories, sorted (symlinks skipped)."""
    target = _listable(path)
    if target is None:
        return []
    return sorted(d.name for d in target.iterdir()
                  if not d.is_symlink() and d.is_dir() and not d.name.startswith("."))


def which(tool: str) -> str | None:
    """Locate an executable on PATH (read-only; kept here so other modules need no shutil)."""
    import shutil
    return shutil.which(tool)


ALLOWED_TOOLS = {"graphify", "claude", "code-review-graph", "ssh-keygen", "gh", "iwe", "iwec"}


def _allowed(argv: list[str]) -> None:
    if Path(argv[0]).name not in ALLOWED_TOOLS:
        raise ValueError(f"{argv[0]} is not an allowed tool")


def run_tool(cwd: Path, argv: list[str], extra_env: dict[str, str] | None = None) -> int:
    """Run an allow-listed external tool in `cwd`, output streamed to the terminal.

    Only tools in ALLOWED_TOOLS may run, and only in a directory that passes the v1 guard
    (the tool writes its output there).
    """
    _allowed(argv)
    target = assert_safe(cwd)
    return subprocess.run(argv, cwd=target, env={**os.environ, **(extra_env or {})}).returncode


def capture_tool(cwd: Path, argv: list[str], stdin: str, quiet: bool = False) -> subprocess.CompletedProcess:
    """Run an allow-listed tool with `stdin`, capturing stdout (stderr goes to the terminal,
    or nowhere with quiet=True)."""
    _allowed(argv)
    return subprocess.run(argv, cwd=assert_safe(cwd), input=stdin, stdout=subprocess.PIPE, text=True,
                          stderr=subprocess.DEVNULL if quiet else None)


def exec_tool(argv: list[str], cwd: Path | None = None) -> None:
    """Replace this process with an allow-listed tool (interactive sessions, MCP servers),
    optionally started in `cwd` (which must pass the v1 guard)."""
    _allowed(argv)
    if cwd is not None:
        os.chdir(assert_safe(cwd))
    os.execvp(argv[0], argv)


def move(src: Path, dst: Path) -> None:
    """Rename src → dst; both sides must pass the guard, neither may be a symlink (a link would
    move or replace what it points at), and dst must not exist."""
    for side in (src, dst):
        if Path(os.path.abspath(side)).is_symlink():
            raise SymlinkInRepoError(f"ERROR: {side} is a symlink — not moved (corkern never follows "
                                     "symlinks in the knowledge repo).")
    source, target = assert_safe(src), assert_safe(dst)
    if os.path.lexists(target):
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)


def walk_files(path: Path, suffix: str = "") -> list[Path]:
    """All regular files below `path` ending in `suffix`, sorted (C order like v1's sort).
    Symlinks — to files or to folders — are skipped, never followed."""
    target = _listable(path)
    if target is None:
        return []
    found = [Path(folder) / name for folder, _, names in os.walk(target)   # followlinks=False
             for name in names if name.endswith(suffix)]
    return sorted((f for f in found if not f.is_symlink() and f.is_file()), key=lambda p: str(p).encode())


def read_bytes(path: Path) -> bytes:
    return assert_safe(path).read_bytes()


def package_dir() -> Path:
    """corkern's own installed package folder (packaged hooks/ and claude_commands/). Read-only."""
    return Path(__file__).resolve().parent


def write_executable(path: Path, content: bytes) -> None:
    """Write a script corkern installs (its own hooks): 0755, atomically replaced."""
    target = assert_safe(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp-corkern")
    tmp.write_bytes(content)
    # bandit B103: hook scripts must be executable by Claude Code; 0755 gives nobody else write access
    os.chmod(tmp, 0o755)  # nosec B103
    os.replace(tmp, target)


def make_dirs(path: Path) -> None:
    """Create a directory (and its parents) that passes the guard."""
    assert_safe(path).mkdir(parents=True, exist_ok=True)


def which_all(name: str) -> list[Path]:
    """Every executable `name` on PATH, in PATH order (duplicates and dead links skipped)."""
    seen, found = set(), []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d or ".") / name
        if p.is_file() and os.access(p, os.X_OK):
            key = p.resolve()
            if key not in seen:
                seen.add(key)
                found.append(p)
    return found
