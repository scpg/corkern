"""corkern signing --check — every broken state is reported with its fix."""
import os
import socket
import subprocess
from pathlib import Path

import pytest
import typer

from corkern.commands import signing_check as sc

ROOT = Path(__file__).resolve().parents[1]
C2 = ROOT / ".venv" / "bin" / "corkern"


@pytest.fixture
def setup(tmp_path):
    """A knowledge repo set up by `corkern signing --yes` (temp HOME) with a local bare remote."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    kr, remote = tmp_path / "kr", tmp_path / "remote.git"
    env = {**os.environ, "HOME": str(home), "CORKERN_MEMORY_DIR": str(kr), "PATH": "/usr/bin:/bin"}
    subprocess.run([str(C2), "init", str(kr)], check=True, capture_output=True, env=env)
    subprocess.run([str(C2), "signing", "--yes"], check=True, capture_output=True, env=env, input="")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "-C", str(kr), "remote", "add", "origin", str(remote)], check=True)
    subprocess.run(["git", "-C", str(kr), "push", "-q", "-u", "origin", "main"], check=True)
    host = socket.gethostname().split(".")[0]
    return {"kr": kr, "env": env, "key": home / ".ssh" / f"corkern_signing_{host}"}


def check(setup):
    return subprocess.run([str(C2), "signing", "--check"], capture_output=True, text=True,
                          env=setup["env"])


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_healthy_local_setup_passes(setup):
    r = check(setup)
    assert r.returncode == 0, r.stdout
    assert "All checks passed" in r.stdout and "not on github.com" in r.stdout


def test_missing_configuration(tmp_path):
    kr = tmp_path / "kr"
    env = {**os.environ, "HOME": str(tmp_path), "CORKERN_MEMORY_DIR": str(kr)}
    subprocess.run([str(C2), "init", str(kr)], check=True, capture_output=True, env=env)
    r = subprocess.run([str(C2), "signing", "--check"], capture_output=True, text=True, env=env)
    assert r.returncode == 1
    assert "no machine signing key configured" in r.stdout and "fix: corkern signing" in r.stdout


def test_unsafe_key_permissions(setup):
    setup["key"].chmod(0o644)
    r = check(setup)
    assert r.returncode == 1 and f"chmod 600 {setup['key']}" in r.stdout


def test_missing_key_file(setup):
    setup["key"].unlink()
    assert "is missing" in check(setup).stdout


def test_interactive_signer_detected(setup):
    git(setup["kr"], "config", "--local", "gpg.ssh.program", "/opt/1Password/op-ssh-sign")
    r = check(setup)
    assert r.returncode == 1 and "through 1Password" in r.stdout


def test_key_not_in_allowed_signers(setup):
    (setup["kr"] / ".devices" / "allowed_signers").write_text("")
    assert "not in .devices/allowed_signers" in check(setup).stdout


def test_unreachable_remote(setup, tmp_path):
    git(setup["kr"], "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    r = check(setup)
    assert r.returncode == 1 and "Cannot reach the remote" in r.stdout


def test_push_key_hints(setup, tmp_path):
    git(setup["kr"], "config", "--local", "core.sshCommand", f"ssh -i {tmp_path}/nokey")
    assert "Push key" in check(setup).stdout and "is missing" in check(setup).stdout


# ── GitHub side: canned API answers ────────────────────────────────────────────

@pytest.fixture
def gh(monkeypatch, tmp_path, capsys):
    push, sign = tmp_path / "push", tmp_path / "sign"
    for k, blob in ((push, "PUSHBLOB"), (sign, "SIGNBLOB")):
        k.write_text("private")
        k.with_suffix(".pub").write_text(f"ssh-ed25519 {blob} c")
    answers = {"user": {"login": "tester"},
               "repos/o/r/keys": [{"title": "corkern-push", "key": "ssh-ed25519 PUSHBLOB", "read_only": False}],
               "user/ssh_signing_keys": [{"key": "ssh-ed25519 SIGNBLOB"}],
               "repos/o/r/commits/abc": {"commit": {"verification": {"verified": True, "reason": "valid"}}}}
    monkeypatch.setattr(sc.guard, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(sc, "_gh_json", lambda gh, repo, path: answers.get(path))
    git_answers = {("remote", "get-url", "origin"): "git@github.com:o/r.git",
                   ("rev-parse", "HEAD"): "abc", ("branch", "-r", "--contains", "abc"): "origin/main",
                   ("log", "-1", "--format=%ae"): "me@example.com"}
    monkeypatch.setattr(sc, "_git", lambda repo, *args, timeout=None: git_answers.get(args))

    def run():
        c = sc.Check()
        sc._github(c, tmp_path, sign, push)
        return c, capsys.readouterr().out
    return answers, run


def test_github_all_good(gh):
    c, out = gh[1]()
    assert c.failed == 0 and c.warned == 0
    assert "can write" in out and "registered on GitHub" in out and "Verified" in out


def test_deploy_key_missing(gh):
    gh[0]["repos/o/r/keys"] = []
    c, out = gh[1]()
    assert c.failed == 1 and "Add deploy key" in out and "Allow write access" in out


def test_deploy_key_read_only(gh):
    gh[0]["repos/o/r/keys"][0]["read_only"] = True
    c, out = gh[1]()
    assert c.failed == 1 and "read-only" in out


def test_signing_key_not_on_github(gh):
    gh[0]["user/ssh_signing_keys"] = []
    c, out = gh[1]()
    assert c.warned >= 1 and "gh ssh-key add" in out and "Signing Key" in out


def test_signing_keys_not_listable(gh):
    del gh[0]["user/ssh_signing_keys"]
    c, out = gh[1]()
    assert "read:ssh_signing_key" in out


@pytest.mark.parametrize("reason,needle,failed", [
    ("unknown_key", "does not know the key", 0),
    ("bad_email", "cannot match the commit email me@example.com", 1),
    ("unsigned", "not Verified on GitHub (unsigned)", 0),
])
def test_commit_not_verified(gh, reason, needle, failed):
    gh[0]["repos/o/r/commits/abc"] = {"commit": {"verification": {"verified": False, "reason": reason}}}
    c, out = gh[1]()
    assert needle in out and c.failed == failed


def test_gh_missing(gh, monkeypatch):
    monkeypatch.setattr(sc.guard, "which", lambda tool: None)
    c, out = gh[1]()
    assert "not checked" in out and "Deploy keys" in out
