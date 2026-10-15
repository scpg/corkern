from __future__ import annotations
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from corkern import guard

CONF_FILE = "corkern.conf"
LOCAL_CONF_FILE = "corkern.conf.local"
MEMORY_SUBDIR = "memory"

# v1 slugs (corkern, toolbox, sd-theProductName, ...) all match; ':' '/' whitespace never do
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_WINDOWS_ABS = re.compile(r"[A-Za-z]:")
# Everything str.splitlines() treats as a line boundary — never allowed inside a stored value
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
# A project path is printed into manifests, Claude's context and shell snippets: never shell code
SHELL_META = "$`\\;|&<>"


def unsafe_path_text(text: str) -> str | None:
    """Why a project path is refused: control characters or shell metacharacters (spaces are fine)."""
    if any(ord(c) < 0x20 or ord(c) == 0x7f or c in LINE_BREAKS for c in text):
        return "contains a control character"
    if any(c in SHELL_META for c in text):
        return "contains a shell metacharacter ($ ` \\ ; | & < >)"
    return None


class UnstorablePath(ValueError):
    pass


def valid_slug(slug: str) -> bool:
    return SLUG_RE.fullmatch(slug) is not None


def conf_lines(text: str) -> list[str]:
    """Physical lines as v1 reads them: split on '\\n' only, trailing '\\r' dropped."""
    return [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]


def conf_entries(text: str) -> Iterator[tuple[str, str]]:
    """(slug, raw path) for every non-comment entry line, in file order."""
    for line in conf_lines(text):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        slug, sep, raw_path = line.partition(":")
        if sep and slug.strip():
            yield slug.strip(), raw_path


def resolve_project_path(raw: str) -> Path | None:
    """v1 projects.conf semantics: empty → HOME, absolute as-is, relative → HOME/raw.

    `~` and `~/...` mean HOME; any other `~name` is relative like in v1 (never another
    user's home). Windows absolute paths (C:/...) belong to Windows devices → None.
    """
    raw = raw.strip()
    if _WINDOWS_ABS.match(raw):
        return None
    if raw in ("", "~"):
        return Path.home()
    if raw.startswith("~/"):
        return Path.home() / raw[2:]
    path = Path(raw)
    return path if path.is_absolute() else Path.home() / path


def store_project_path(path: Path) -> str:
    """Inverse of resolve_project_path: HOME-relative when that round-trips, so the file is
    portable between devices with different home directories; absolute otherwise."""
    text = str(path)
    if any(c in text for c in LINE_BREAKS):
        raise UnstorablePath("contains a line break")
    if unsafe_path_text(text):
        raise UnstorablePath(unsafe_path_text(text))
    if text != text.strip():
        raise UnstorablePath("has leading or trailing whitespace")
    for home in (Path.home(), Path.home().resolve()):  # HOME may be a symlink
        if path == home:
            return ""
        if path.is_relative_to(home):
            rel = str(path.relative_to(home))
            if not _WINDOWS_ABS.match(rel) and not rel.startswith("~"):
                return rel
            break
    return text


class MemoryRepo:
    """A v2 knowledge repo. Memory files live under <repo>/memory/<slug>/."""

    def __init__(self, path: Path) -> None:
        self._path = guard.assert_safe(path)
        guard.set_knowledge_repo(path)    # nothing below it is reached through a symlink
        # Checked separately: a memory/ symlinked into v1 is refused outright; one pointing
        # anywhere else is treated as absent (listing returns nothing, see guard)
        try:
            self._memory_dir = guard.assert_safe(self._path / MEMORY_SUBDIR)
        except guard.SymlinkInRepoError:
            self._memory_dir = self._path / MEMORY_SUBDIR

    @property
    def path(self) -> Path:
        return self._path

    @property
    def memory_dir(self) -> Path:
        return self._memory_dir

    @property
    def conf_file(self) -> Path:
        return self._path / CONF_FILE

    def read_conf(self) -> str:
        """corkern.conf's text; a symlinked corkern.conf counts as empty (see repo_symlinks)."""
        return guard.read_shared(self.conf_file) or ""

    def slugs(self) -> list[str]:
        return guard.list_dirs(self._memory_dir)

    def _memory_file(self, slug: str, filename: str) -> Path:
        target = Path(os.path.abspath(self._memory_dir / slug / filename))
        if not target.is_relative_to(self._memory_dir) or target == self._memory_dir:
            raise ValueError(f"Path traversal blocked: {target}")
        return target

    def read(self, slug: str, filename: str) -> str:
        return guard.read_text(self._memory_file(slug, filename))

    def write(self, slug: str, filename: str, content: str) -> None:
        guard.write_text(self._memory_file(slug, filename), content)


def repo_state_dir(repo: Path) -> Path:
    """This machine's sync state for a knowledge repo (lock, last-sync-*, held, trust store):
    inside .git, never in the work tree, where anyone who can push could plant a symlink.
    Same place as hooks/lib.sh $CK_STATE."""
    return repo / ".git" / "corkern"


_URL_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]*@")


def safe_url(url: str) -> str:
    """A remote URL fit to print or store: user:password@ / token@ removed."""
    return _URL_USERINFO.sub(r"\1", url)


def state_dir() -> Path:
    """Per-machine state outside the knowledge repo (never synced) — same as hooks/lib.sh."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "corkern"


def machine_conf_path() -> Path:
    """This machine's corkern settings (never synced, not part of any dotfiles):
    $XDG_CONFIG_HOME/corkern/machine.conf, default ~/.config/corkern/machine.conf."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "corkern" / "machine.conf"


def machine_memory_dir() -> str | None:
    """The knowledge repo recorded for this machine by `corkern setup`, if any."""
    conf = machine_conf_path()
    if not conf.is_file():
        return None
    for line in guard.read_text(conf).splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "memory_dir" and value.strip():
            return value.strip()
    return None


def write_machine_conf(memory_dir: Path) -> Path:
    conf = machine_conf_path()
    guard.write_text(conf, "# corkern — this machine only (written by `corkern setup`; not synced)\n"
                           f"memory_dir={memory_dir}\n")
    return conf


def memory_dir_setting() -> str | None:
    """CORKERN_MEMORY_DIR if set, else this machine's machine.conf."""
    return os.environ.get("CORKERN_MEMORY_DIR") or machine_memory_dir()


def load_config() -> tuple[MemoryRepo, dict[str, Path]]:
    memory_dir_env = memory_dir_setting()
    if not memory_dir_env:
        raise SystemExit(
            "ERROR: no knowledge repo set for this machine.\n"
            "Run: corkern setup --clone <url> (or --memory-dir PATH), or set CORKERN_MEMORY_DIR."
        )

    memory_dir = guard.assert_safe(memory_dir_env)
    if not memory_dir.is_dir():
        raise SystemExit(f"ERROR: the knowledge repo {memory_dir} does not exist.")
    conf = memory_dir / CONF_FILE
    if not (conf.is_file() or conf.is_symlink()):     # a symlinked one is read as empty
        raise SystemExit(
            f"ERROR: {memory_dir} is not a corkern v2 knowledge repo (no {CONF_FILE})."
        )

    repo = MemoryRepo(memory_dir)
    check_format(repo.path)
    return repo, _load_projects(repo.path)


def repo_format(repo_path: Path) -> int | None:
    """The knowledge repo's format version (None: no marker — repos made before markers)."""
    text = guard.read_shared(repo_path / ".corkern-format")
    if text is None:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "knowledge_repo_format" and value.strip().isdigit():
            return int(value.strip())
    return None


def check_format(repo_path: Path) -> None:
    from corkern.knowledge_repo import FORMAT_VERSION
    found = repo_format(repo_path)
    if found is not None and found > FORMAT_VERSION:
        raise SystemExit(
            f"ERROR: {repo_path} uses knowledge repo format {found}; this corkern understands up to "
            f"{FORMAT_VERSION}.\nUpdate corkern (install.sh) on this machine before using it.")


def _valid_entries(text: str, source: str, warn: bool = True) -> Iterator[tuple[str, str, Path]]:
    """(slug, raw path, resolved path) for every usable line, in file order (duplicates too)."""
    for slug, raw_path in conf_entries(text):
        if not valid_slug(slug):
            if warn:
                print(f"warning: {source}: skipping invalid slug {slug!r}", file=sys.stderr)
            continue
        path = resolve_project_path(raw_path)
        if path is None:
            continue
        unsafe = unsafe_path_text(raw_path)
        if unsafe:
            if warn:
                print(f"warning: {source}: skipping {slug!r}: its path {unsafe}", file=sys.stderr)
            continue
        yield slug, raw_path, path


def _parse_conf(text: str, source: str) -> dict[str, Path]:
    """First line per slug wins (a slug may have one line per device; v1 uses head -1)."""
    entries: dict[str, Path] = {}
    for slug, _, path in _valid_entries(text, source):
        entries.setdefault(slug, path)
    return entries


def _load_projects(repo_path: Path) -> dict[str, Path]:
    projects: dict[str, Path] = {}
    for name in (CONF_FILE, LOCAL_CONF_FILE):  # local overrides
        text = guard.read_shared(repo_path / name)       # a symlinked one counts as absent
        if text is not None:
            projects.update(_parse_conf(text, name))
    return projects


# ── projects this machine confirmed (audit M8, L1) ──────────────────────────────────────
# corkern.conf is shared: anyone who can push can point a slug at any folder of this machine.
# Whatever reads a project's private data (its Claude Code transcripts) or wires its folder
# uses a slug's path only when this machine confirmed it: `corkern add`, corkern.conf.local
# (this machine's own file), a per-project "y" in init-device, or migrate for the projects of
# this machine's own export. Kept like the trust store: in .git/corkern, never synced.
CONFIRMED_FILE = "projects-confirmed"
NOT_CONFIRMED = "not confirmed on this machine"


def confirmed_file(repo: Path) -> Path:
    return repo_state_dir(repo) / CONFIRMED_FILE


def read_confirmed(repo: Path) -> dict[str, Path]:
    """slug → path this machine confirmed ("slug<TAB>absolute path" lines; a symlinked file is
    read as empty)."""
    out: dict[str, Path] = {}
    for line in (guard.read_shared(confirmed_file(repo)) or "").splitlines():
        slug, sep, path = line.partition("\t")
        if sep and valid_slug(slug) and path.startswith("/") and not unsafe_path_text(path):
            out[slug] = Path(path)
    return out


def confirm_projects(repo: Path, items: dict[str, Path]) -> None:
    """Record `items` as confirmed on this machine (0600, never through a symlink)."""
    if not items:
        return
    guard.set_knowledge_repo(repo)
    confirmed = {**read_confirmed(repo), **items}
    guard.write_private(confirmed_file(repo), "".join(f"{s}\t{p}\n" for s, p in sorted(confirmed.items())))


def same_path(a: Path, b: Path) -> bool:
    return os.path.realpath(a) == os.path.realpath(b)


@dataclass(frozen=True)
class ProjectCheck:
    slug: str
    path: Path | None                 # the path to read from / wire; None: not confirmed
    candidates: tuple[Path, ...]      # the different paths the registry gives this machine
    lines: tuple[str, ...]            # the registry lines they come from
    local: bool = False               # from corkern.conf.local (this machine's own)

    @property
    def confirmed(self) -> bool:
        return self.path is not None

    @property
    def duplicate(self) -> bool:
        return len(self.candidates) > 1

    def explain(self) -> str:
        """Why the path is not used, and how to confirm it."""
        shown = ", ".join(f"'{line}'" for line in self.lines)
        what = (f"{len(self.candidates)} different paths in corkern.conf ({shown}), none"
                if self.duplicate else f"corkern.conf says {shown} → {self.candidates[0]}, which is")
        fixes = "\n".join(f"    corkern add {self.slug} {p}" for p in self.candidates)
        return (f"'{self.slug}': {what} {NOT_CONFIRMED}.\n"
                f"  If it is right, run on this machine to confirm:\n{fixes}")


def check_projects(repo: Path) -> dict[str, ProjectCheck]:
    """Every registered slug: which path, if any, this machine confirmed."""
    local = _parse_conf(guard.read_shared(repo / LOCAL_CONF_FILE) or "", LOCAL_CONF_FILE)
    shared: dict[str, list[tuple[str, Path]]] = {}
    for slug, raw, path in _valid_entries(guard.read_shared(repo / CONF_FILE) or "", CONF_FILE, warn=False):
        group = shared.setdefault(slug, [])
        if not any(same_path(path, p) for _, p in group):
            group.append((f"{slug}:{raw}", path))
    confirmed = read_confirmed(repo)
    checks: dict[str, ProjectCheck] = {}
    for slug in sorted({*shared, *local}):
        group = shared.get(slug, [])
        lines, candidates = tuple(line for line, _ in group), tuple(p for _, p in group)
        if slug in local:
            checks[slug] = ProjectCheck(slug, local[slug], (local[slug],), (f"{slug}:… (corkern.conf.local)",),
                                        local=True)
            continue
        mine = confirmed.get(slug)
        path = next((p for p in candidates if mine is not None and same_path(p, mine)), None)
        checks[slug] = ProjectCheck(slug, path, candidates, lines)
    return checks
