"""status / doctor health checks."""
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corkern.main import app

runner = CliRunner()


@pytest.fixture
def kr(tmp_path):
    repo = tmp_path / "kr"
    assert runner.invoke(app, ["init", str(repo)]).exit_code == 0
    return repo


def status(repo, **env):
    return runner.invoke(app, ["status"], env={"CORKERN_MEMORY_DIR": str(repo), **env})


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_fresh_repo_is_healthy_with_warnings(kr):
    r = status(kr)
    assert r.exit_code == 0
    assert "No remote configured" in r.output
    assert "never recorded" in r.output
    assert "union merge" not in r.output          # template ships .gitattributes


def test_doctor_alias(kr):
    assert "Knowledge repo" in runner.invoke(app, ["doctor"], env={"CORKERN_MEMORY_DIR": str(kr)}).output


def state(repo, name):
    """A file in the repo's per-machine sync state (hooks/lib.sh $CK_STATE)."""
    d = repo / ".git" / "corkern"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def test_stuck_rebase_fails(kr):
    (kr / ".git" / "rebase-merge").mkdir()
    r = status(kr)
    assert r.exit_code == 1
    assert "stuck in a rebase/merge" in r.output


def test_reports_sync_errors_and_conflicts(kr):
    state(kr, "last-sync-push-error").write_text("timestamp : x\nreason    : git push failed after retry\n")
    state(kr, "last-sync-pull").write_text("2026-10-01 10:00:00")
    state(kr, "last-sync-conflicts").write_text("a kept\nb kept\n")
    r = status(kr)
    assert "Last push failed: git push failed after retry" in r.output
    assert "Last pull: 2026-10-01 10:00:00" in r.output
    assert "2 conflicting edit(s)" in r.output
    assert r.exit_code == 1                        # a failing push needs attention


def test_reports_last_hook_problem(kr, tmp_path):
    state = tmp_path / "state" / "corkern"
    state.mkdir(parents=True)
    (state / "last-hook-problem").write_text("2026-10-01 commit-memory: lock busy\n")
    r = status(kr, XDG_STATE_HOME=str(tmp_path / "state"))
    assert "Last hook problem: 2026-10-01 commit-memory: lock busy" in r.output


def test_warns_about_interactive_signing(kr):
    git(kr, "config", "commit.gpgsign", "true")
    git(kr, "config", "gpg.ssh.program", "/opt/1Password/op-ssh-sign")
    assert "1Password (interactive)" in status(kr).output


def test_remote_in_sync_and_ahead(kr, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    git(kr, "remote", "add", "origin", str(remote))
    git(kr, "push", "-q", "-u", "origin", "main")
    assert "In sync with the remote" in status(kr).output
    (kr / "memory" / "global" / "x.md").write_text("x")
    git(kr, "add", "-A")
    git(kr, "commit", "-qm", "x")
    assert "1 commit(s) not pushed yet" in status(kr).output


def test_unreachable_remote_is_a_warning(kr, tmp_path):
    git(kr, "remote", "add", "origin", str(tmp_path / "gone.git"))
    r = status(kr)
    assert "Remote not reachable" in r.output
    assert r.exit_code == 0


def test_missing_gitattributes_warned(kr):
    (kr / ".gitattributes").unlink()
    assert "union merge" in status(kr).output
