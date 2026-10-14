"""setup — this machine's knowledge repo goes into machine.conf (not ~/.claude, not the env)."""
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corkern.main import app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CORKERN_MEMORY_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("CORKERN_CODE_DIR", str(h / ".local/share/corkern"))
    return h


def run(*args):
    return CliRunner().invoke(app, list(args))


def conf(home):
    return home / ".config" / "corkern" / "machine.conf"


def test_new_records_the_repo_and_installs_hooks(home):
    r = run("setup", "--new")
    assert r.exit_code == 0, r.output
    assert f"memory_dir={home / '.corkern'}" in conf(home).read_text()
    assert (home / ".corkern" / "corkern.conf").is_file()
    assert (home / ".local/share/corkern/hooks/corkern.sh").is_file()
    assert run("list").exit_code == 0                          # no CORKERN_MEMORY_DIR needed


def test_existing_memory_dir(home, tmp_path):
    kr = tmp_path / "kr"
    assert run("init", str(kr)).exit_code == 0
    r = run("setup", "--memory-dir", str(kr))
    assert r.exit_code == 0, r.output
    assert f"memory_dir={kr}" in conf(home).read_text()


def test_existing_needs_a_knowledge_repo(home):
    r = run("setup")
    assert r.exit_code == 1 and "no knowledge repo at" in r.output and not conf(home).exists()


def test_clone(home, tmp_path):
    src = tmp_path / "src"
    assert run("init", str(src)).exit_code == 0
    bare = tmp_path / "kr.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    r = run("setup", "--clone", str(bare))
    assert r.exit_code == 0, r.output
    assert (home / ".corkern" / "corkern.conf").is_file()


def test_clone_of_something_else_is_refused(home, tmp_path):
    other = tmp_path / "other"
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    r = run("setup", "--clone", str(other))
    assert r.exit_code == 1 and "not a corkern knowledge repo" in r.output and not conf(home).exists()


def test_never_overwrites_a_non_empty_folder(home):
    (home / ".corkern").mkdir()
    (home / ".corkern" / "mine.txt").write_text("x")
    r = run("setup", "--new")
    assert r.exit_code == 1 and "not empty" in r.output


def test_one_repo_per_machine(home, tmp_path):
    assert run("setup", "--new").exit_code == 0
    kr = tmp_path / "kr2"
    assert run("init", str(kr)).exit_code == 0
    r = run("setup", "--memory-dir", str(kr))
    assert r.exit_code == 1 and "already uses" in r.output
    assert f"memory_dir={home / '.corkern'}" in conf(home).read_text()


def test_environment_still_wins(home, tmp_path, monkeypatch):
    assert run("setup", "--new").exit_code == 0
    other = tmp_path / "other-kr"
    assert run("init", str(other)).exit_code == 0
    monkeypatch.setenv("CORKERN_MEMORY_DIR", str(other))
    assert str(other) in run("status").output


def test_hooks_find_the_repo_in_machine_conf(home):
    assert run("setup", "--new").exit_code == 0
    kr = home / ".corkern"
    (kr / "memory" / "p").mkdir(parents=True)
    (kr / "memory" / "p" / "note.md").write_text("from a hook\n")
    env = {k: v for k, v in os.environ.items() if k != "CORKERN_MEMORY_DIR"}
    env.update(HOME=str(home), CORKERN_HOOK_FOREGROUND="1", XDG_STATE_HOME=str(home / "state"),
               GIT_CONFIG_GLOBAL="/dev/null", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    hook = home / ".local/share/corkern/hooks/corkern.sh"
    r = subprocess.run(["bash", str(hook), "corkern-commit-memory"], input="{}", env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    log = subprocess.run(["git", "-C", str(kr), "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
    assert "p/note.md" in log
