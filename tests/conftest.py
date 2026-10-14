import os
import pytest
from pathlib import Path

# The real user's Claude Code and corkern install files, as they were before any test changed the
# environment. init-device writes exactly these; a test that reaches them would wire v2 into the
# Claude Code of the machine running the tests (CLAUDE.md, coexistence rule 3).
_REAL_HOME = Path(os.environ.get("HOME") or Path.home())
_REAL_CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or _REAL_HOME / ".claude").expanduser()
_REAL_USER_FILES = [_REAL_CLAUDE / "settings.json", _REAL_CLAUDE / "CLAUDE.md", _REAL_CLAUDE / "commands",
                    _REAL_HOME / ".local" / "share" / "corkern", _REAL_HOME / ".local" / "state" / "corkern"]


def _snapshot(paths: list[Path]) -> dict[str, object]:
    """Existence, size and mtime of each path; for a folder, the same for every entry in it."""
    snap: dict[str, object] = {}
    for path in paths:
        entries = [path] + (sorted(path.rglob("*")) if path.is_dir() else [])
        for entry in entries:
            try:
                st = entry.lstat()
                snap[str(entry)] = (st.st_size, st.st_mtime_ns)
            except FileNotFoundError:
                snap[str(entry)] = None
    return snap


@pytest.fixture(autouse=True)
def real_user_files_untouched():
    """Fails any test that creates, changes or deletes the real user's Claude Code / corkern files."""
    before = _snapshot(_REAL_USER_FILES)
    yield
    after = _snapshot(_REAL_USER_FILES)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert not changed, f"test touched the real user's files (isolate HOME): {changed}"


@pytest.fixture(autouse=True)
def dev_checkout(monkeypatch):
    """Tests run corkern from this checkout's .venv, not a tool install (see corkern.instance)."""
    monkeypatch.setenv("CORKERN_DEV", "1")


@pytest.fixture(autouse=True)
def isolated_machine_conf(tmp_path_factory, monkeypatch):
    """Never read the real machine.conf of the machine running the tests (corkern setup)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-config")))


@pytest.fixture(autouse=True)
def isolated_git(tmp_path_factory, monkeypatch):
    """Tests never see the user's git config (1Password signing would block)."""
    cfg = tmp_path_factory.mktemp("gitcfg") / "gitconfig"
    cfg.write_text("[user]\n\tname = corkern-test\n\temail = test@corkern.invalid\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def test_repo(tmp_path: Path) -> Path:
    """A minimal corkern knowledge repo (spec layout) with two projects."""
    (tmp_path / "corkern.conf").write_text("alpha:/tmp/alpha\nbeta:/tmp/beta\n")
    (tmp_path / "memory" / "alpha").mkdir(parents=True)
    (tmp_path / "memory" / "beta").mkdir(parents=True)
    (tmp_path / ".devices").mkdir()
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def v1_repo(tmp_path: Path) -> Path:
    """A v1-protected repo (has .corkern-v1.sentinel)."""
    (tmp_path / ".corkern-v1.sentinel").touch()
    return tmp_path


def list_row(output: str, slug: str) -> str | None:
    """The `corkern list` row of `slug` on this device, if any."""
    return next((line for line in output.splitlines()
                 if line.startswith(f"  {slug} ") and "(local)" in line), None)


def listed_here(output: str, slug: str) -> bool:
    """`slug` is listed for this device and its path exists here."""
    row = list_row(output, slug)
    return row is not None and "no path" not in row
