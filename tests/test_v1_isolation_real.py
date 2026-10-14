"""Live checks against the real v1 repo on this machine (skipped where v1 is absent, e.g. CI).

Every test first proves the guard refuses the path *before* any CLI command runs,
so a guard regression fails here without ever reaching v1.
"""
import os
import pwd
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corkern import guard
from corkern.guard import V1ProtectedError
from corkern.main import app

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
V1 = REAL_HOME / "dev" / "corkern"

pytestmark = pytest.mark.skipif(not (V1 / "projects.conf").exists(), reason="no v1 repo here")


def _v1_memory_symlinks() -> list[Path]:
    projects = REAL_HOME / ".claude" / "projects"
    links = [p / "memory" for p in projects.glob("*") if (p / "memory").is_symlink()]
    return [l for l in links if str(l.resolve()).startswith(str(V1))][:3]


def _v1_paths() -> list[Path]:
    return [V1, V1 / "memory", V1 / "memory" / "corkern", V1 / "memory" / "global",
            *_v1_memory_symlinks()]


def _snapshot() -> str:
    git = lambda *a: subprocess.run(["git", "-C", str(V1), *a], capture_output=True, text=True).stdout
    return git("rev-parse", "HEAD") + git("--no-optional-locks", "status", "--porcelain")


@pytest.fixture(scope="module", autouse=True)
def v1_unchanged():
    before = _snapshot()
    yield
    assert _snapshot() == before, "v1 repo state changed during v2 tests"


@pytest.mark.parametrize("path", _v1_paths(), ids=str)
def test_guard_refuses_real_v1(path):
    with pytest.raises(V1ProtectedError):
        guard.assert_safe(path)


@pytest.mark.parametrize("path", _v1_paths(), ids=str)
@pytest.mark.parametrize("args", [["list"], ["status"], ["add", "zz-v2-probe", "/tmp"], ["remove", "corkern"]])
def test_cli_refuses_real_v1(path, args):
    with pytest.raises(V1ProtectedError):
        guard.assert_safe(path)  # precondition — never reach the CLI if this fails
    result = CliRunner().invoke(app, args, env={"CORKERN_MEMORY_DIR": str(path)})
    assert result.exit_code != 0
    assert "v1-protected" in result.output


def test_refused_even_with_home_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CORKERN", raising=False)
    with pytest.raises(V1ProtectedError):
        guard.assert_safe(V1 / "memory")


def test_refused_v1_memory_symlink_target_from_test_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "corkern.conf").write_text("")
    (repo / "memory").symlink_to(V1 / "memory")
    result = CliRunner().invoke(app, ["list"], env={"CORKERN_MEMORY_DIR": str(repo)})
    assert result.exit_code != 0
    assert "v1-protected" in result.output


def test_v1_untouched_by_probe_add():
    conf = (V1 / "projects.conf").read_text()
    assert "zz-v2-probe" not in conf
    assert not (V1 / "memory" / "corkern.conf").exists()
