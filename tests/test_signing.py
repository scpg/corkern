"""corkern signing — one key per machine; nothing happens without the user's approval."""
import os
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
C2 = ROOT / ".venv" / "bin" / "corkern"
GH_STUB = '#!/usr/bin/env bash\necho "gh $*" >> "$GH_LOG"\n'


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    kr = tmp_path / "kr"
    base = {**os.environ, "HOME": str(home)}
    subprocess.run([str(C2), "init", str(kr)], check=True, capture_output=True, env=base)
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "gh").write_text(GH_STUB)
    (stub / "gh").chmod(0o755)
    host = socket.gethostname().split(".")[0]
    return {"home": home, "kr": kr, "key": home / ".ssh" / f"corkern_signing_{host}",
            "gh_log": tmp_path / "gh.log",
            "env": {**base, "CORKERN_MEMORY_DIR": str(kr), "GH_LOG": str(tmp_path / "gh.log"),
                    "PATH": f"{stub}:{os.environ['PATH']}"}}


def run(env, *args, stdin="", **extra):
    return subprocess.run([str(C2), "signing", *args], input=stdin, capture_output=True,
                          text=True, env={**env["env"], **extra}, timeout=60)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout.strip()


def test_asks_and_does_nothing_without_approval(env):
    r = run(env)                                         # no answer
    assert "Proceed? [y/N]" in r.stdout and "Nothing changed." in r.stdout
    assert not env["key"].exists()
    assert git(env["kr"], "config", "--local", "--get", "user.signingKey") == ""


def test_declining_shows_what_to_do(env):
    r = run(env, stdin="n\n")
    assert "To set it up later, run:   corkern signing" in r.stdout
    assert f"ssh-keygen -t ed25519 -N \"\"" in r.stdout and str(env["key"]) in r.stdout
    assert "config --local user.signingKey" in r.stdout


def test_approved_setup_signs_and_verifies(env):
    r = run(env, stdin="y\nn\n")
    assert r.returncode == 0, r.stderr
    key = env["key"]
    assert key.is_file() and oct(key.stat().st_mode)[-3:] == "600"
    assert git(env["kr"], "config", "--local", "user.signingKey") == str(key)
    assert "Signed test commit verified" in r.stdout
    assert git(env["kr"], "log", "-1", "--format=%G?") == "G"           # good signature
    signers = (env["kr"] / ".devices" / "allowed_signers").read_text()
    assert key.with_suffix(".pub").read_text().split()[1] in signers
    assert ".devices/allowed_signers merge=union" in (env["kr"] / ".gitattributes").read_text()


def test_hook_commits_are_signed_with_machine_key(env):
    run(env, "--yes", stdin="n\n")
    (env["kr"] / "memory" / "p").mkdir(parents=True)
    (env["kr"] / "memory" / "p" / "x.md").write_text("x")
    subprocess.run(["bash", str(ROOT / "src" / "corkern" / "hooks" / "corkern.sh"), "corkern-commit-memory"],
                   input="{}", text=True, env={**env["env"], "CORKERN_HOOK_FOREGROUND": "1"})
    assert git(env["kr"], "log", "-1", "--format=%s %G?") == "memory: p/x.md G"


def test_never_overwrites_an_existing_key(env):
    env["key"].write_text("my existing key")
    env["key"].with_suffix(".pub").write_text("ssh-ed25519 AAAAexisting corkern")
    r = run(env, stdin="n\n")
    assert "use the existing key" in r.stdout
    assert env["key"].read_text() == "my existing key"


def test_rerun_is_idempotent(env):
    run(env, "--yes", stdin="n\n")
    before = git(env["kr"], "rev-parse", "HEAD")
    r = run(env, stdin="n\n")
    assert "Already set up" in r.stdout
    assert git(env["kr"], "rev-parse", "HEAD") == before


def test_github_needs_its_own_approval(env):
    r = run(env, "--yes", stdin="n\n")                   # setup yes, GitHub no
    assert not env["gh_log"].exists()
    assert "gh ssh-key add" in r.stdout and "Key type: \"Signing Key\"" in r.stdout


def test_github_registration_when_approved(env):
    run(env, "--yes", stdin="y\n")
    assert f"ssh-key add {env['key']}.pub --type signing --title corkern-signing" in env["gh_log"].read_text()


def test_without_gh_shows_web_instructions(env, tmp_path):
    import shutil
    nogh = tmp_path / "nogh"                 # only what corkern needs — gh lives in /usr/bin
    nogh.mkdir()
    for tool in ("git", "ssh-keygen"):
        (nogh / tool).symlink_to(shutil.which(tool))
    r = run(env, "--yes", PATH=str(nogh))
    assert "Settings → SSH and GPG keys" in r.stdout and "gh ssh-key add" not in r.stdout


def test_status_reports_machine_key(env):
    run(env, "--yes", stdin="n\n")
    out = subprocess.run([str(C2), "status"], capture_output=True, text=True, env=env["env"]).stdout
    assert "Commits signed with this machine's key" in out


# ── push (deploy) key step ─────────────────────────────────────────────────────

@pytest.fixture
def github_env(env, tmp_path):
    """Remote looks like GitHub (o/r) but git is redirected to a local bare repo."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    for url in ("git@github.com:o/r.git", "https://github.com/o/r.git"):
        subprocess.run(["git", "-C", str(env["kr"]), "config", "--local", "--add", f"url.{remote}.insteadOf", url], check=True)
    subprocess.run(["git", "-C", str(env["kr"]), "remote", "add", "origin", "https://github.com/o/r.git"], check=True)
    env["env"]["CORKERN_DEVICE_NAME"] = "box1"
    env["push"] = env["home"] / ".ssh" / "corkern_push_box1"
    return env


def test_push_key_declined_explains(github_env):
    r = run(github_env, "--yes", stdin="n\nn\n")
    assert "Set up the push key? [y/N]" in r.stdout
    assert "Deploy keys → Add deploy key" in r.stdout and "Allow write access" in r.stdout
    assert not github_env["push"].exists()


def test_push_key_approved_with_gh(github_env):
    r = run(github_env, "--yes", "--deploy-key", stdin="n\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert github_env["push"].is_file()
    call = github_env["gh_log"].read_text()
    assert f"repo deploy-key add {github_env['push']}.pub --allow-write --title corkern-push box1 -R o/r" in call
    assert git(github_env["kr"], "config", "--local", "core.sshCommand") == \
        f"ssh -i {github_env['push']} -o IdentitiesOnly=yes -o IdentityAgent=none"
    assert git(github_env["kr"], "config", "--get", "remote.origin.url") == "git@github.com:o/r.git"
    assert "Remote reachable with the push key" in r.stdout


def test_push_key_without_gh_shows_manual_github_step(github_env, tmp_path):
    import shutil
    nogh = tmp_path / "nogh2"
    nogh.mkdir()
    for tool in ("git", "ssh-keygen"):
        (nogh / tool).symlink_to(shutil.which(tool))
    r = run(github_env, "--yes", "--deploy-key", PATH=str(nogh))
    assert github_env["push"].is_file() and "add the deploy key on GitHub by hand" in r.stdout


def test_device_name_override_and_validation(github_env):
    run(github_env, "--yes", stdin="n\nn\n")
    assert (github_env["home"] / ".ssh" / "corkern_signing_box1").is_file()
    r = run(github_env, CORKERN_DEVICE_NAME="bad name/../x")
    assert r.returncode != 0 and "Invalid device name" in r.stderr


def test_push_key_setup_is_idempotent(github_env):
    run(github_env, "--yes", "--deploy-key", stdin="n\n")
    r = run(github_env, stdin="n\n")
    assert "Push key already set up" in r.stdout
    assert github_env["gh_log"].read_text().count("deploy-key add") == 1


def test_push_key_ssh_unreachable_keeps_the_remote(github_env):
    """Port 22 blocked (host-c has no SSH login key; HTTPS via gh): nothing is switched."""
    kr = github_env["kr"]
    subprocess.run(["git", "-C", str(kr), "config", "--local", "--unset-all", "url." + str(github_env["kr"].parent / "remote.git") + ".insteadOf",
                    "git@github.com:o/r.git"], check=False)
    subprocess.run(["git", "-C", str(kr), "config", "--local", "url./nonexistent/blocked.git.insteadOf", "git@github.com:o/r.git"], check=True)
    r = run(github_env, "--yes", "--deploy-key", stdin="n\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not reachable over SSH" in r.stdout and "nothing switched" in r.stdout
    assert git(kr, "config", "--get", "remote.origin.url") == "https://github.com/o/r.git"
    assert subprocess.run(["git", "-C", str(kr), "config", "--local", "core.sshCommand"], capture_output=True).returncode == 1


def test_push_key_rerun_when_deploy_key_already_on_github(github_env, tmp_path):
    """gh refuses the same key twice; the key is listed → continue instead of failing."""
    run(github_env, "--yes", "--deploy-key", stdin="n\n")
    subprocess.run(["git", "-C", str(github_env["kr"]), "config", "--local", "--unset", "core.sshCommand"], check=True)
    pub = (github_env["push"].with_suffix(".pub")).read_text().split()
    stub = github_env["gh_log"].parent / "bin" / "gh"
    stub.write_text('#!/usr/bin/env bash\necho "gh $*" >> "$GH_LOG"\n'
                    f'case "$*" in *"deploy-key add"*) exit 1 ;; *"deploy-key list"*) echo "{pub[0]} {pub[1]}" ;; esac\n')
    r = run(github_env, "--deploy-key", stdin="n\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "is already on o/r" in r.stdout and "now pushes with it" in r.stdout
