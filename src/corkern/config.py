from __future__ import annotations
import os
import re
import sys
from collections.abc import Iterator
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
        # Checked separately: memory/ may be a symlink pointing somewhere else
        self._memory_dir = guard.assert_safe(self._path / MEMORY_SUBDIR)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def memory_dir(self) -> Path:
        return self._memory_dir

    @property
    def conf_file(self) -> Path:
        return self._path / CONF_FILE

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
    if not (memory_dir / CONF_FILE).is_file():
        raise SystemExit(
            f"ERROR: {memory_dir} is not a corkern v2 knowledge repo (no {CONF_FILE})."
        )

    repo = MemoryRepo(memory_dir)
    check_format(repo.path)
    return repo, _load_projects(repo.path)


def repo_format(repo_path: Path) -> int | None:
    """The knowledge repo's format version (None: no marker — repos made before markers)."""
    marker = repo_path / ".corkern-format"
    if not marker.is_file():
        return None
    for line in guard.read_text(marker).splitlines():
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


def _parse_conf(text: str, source: str) -> dict[str, Path]:
    """First line per slug wins (a slug may have one line per device; v1 uses head -1)."""
    entries: dict[str, Path] = {}
    for slug, raw_path in conf_entries(text):
        if not valid_slug(slug):
            print(f"warning: {source}: skipping invalid slug {slug!r}", file=sys.stderr)
            continue
        if slug in entries:
            continue
        path = resolve_project_path(raw_path)
        if path is not None:
            entries[slug] = path
    return entries


def _load_projects(repo_path: Path) -> dict[str, Path]:
    projects: dict[str, Path] = {}
    for name in (CONF_FILE, LOCAL_CONF_FILE):  # local overrides
        conf_file = repo_path / name
        if conf_file.is_file():
            projects.update(_parse_conf(guard.read_text(conf_file), name))
    return projects
