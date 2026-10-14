"""signing — one commit-signing key per machine for the knowledge repo.

Hook commits happen in the background, so they need a non-interactive signer (the user's
global git signing may go through 1Password, which prompts). This command:
  - creates ~/.ssh/corkern_signing_<host> (ed25519, no passphrase) if missing — never
    overwrites an existing key
  - configures the knowledge repo LOCALLY to sign with it via ssh-keygen (global git
    config is untouched)
  - adds the public key to .devices/allowed_signers (synced, union-merged), so every
    machine can verify every machine's commits
  - makes a signed commit and verifies it as a self-test
  - optionally adds the key to the GitHub account as a signing key
  - optionally sets up a per-machine PUSH key: a deploy key with write access to this one
    repo only, used by corkern alone (core.sshCommand, no SSH agent / 1Password)

Nothing happens without the user's explicit approval: setup and GitHub registration are
each a [y/N] question (default No; no terminal = No). --yes / --register-github /
--deploy-key are the user pre-approving on the command line. Declining prints what to do.
The machine name is the host name, or CORKERN_DEVICE_NAME when set.
"""
from __future__ import annotations

import os
import re
import socket
import sys
from pathlib import Path

import typer

from corkern import guard
from corkern.commands.reminders import fail
from corkern.config import load_config

SIGNERS = ".devices/allowed_signers"


GITHUB_REMOTE = re.compile(r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?$")


def device_name() -> str:
    name = os.environ.get("CORKERN_DEVICE_NAME") or socket.gethostname().split(".")[0] or "device"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        fail(f"Invalid device name {name!r} (letters, digits, '.', '_', '-').")
    return name


def key_path() -> Path:
    return Path.home() / ".ssh" / f"corkern_signing_{device_name()}"


def push_key_path() -> Path:
    return Path.home() / ".ssh" / f"corkern_push_{device_name()}"


def _ask(question: str, preapproved: bool) -> bool:
    if preapproved:
        return True
    typer.echo(question, nl=False)
    answer = sys.stdin.readline().strip() if not sys.stdin.closed else ""
    typer.echo("" if answer else "(no answer → no)")
    return answer in ("y", "Y", "yes")


def signing(
    show: bool = typer.Option(False, "--show", help="Show the current setup; change nothing"),
    check: bool = typer.Option(False, "--check",
                               help="Verify the setup locally and on GitHub; change nothing"),
    yes: bool = typer.Option(False, "--yes", help="Approve the setup without asking"),
    register_github: bool = typer.Option(False, "--register-github",
                                         help="Approve adding the key to GitHub without asking"),
    deploy_key: bool = typer.Option(False, "--deploy-key",
                                    help="Approve the per-machine push (deploy) key without asking"),
) -> None:
    """Set up this machine's signing key for knowledge-repo commits (asks before doing anything)."""
    repo, _ = load_config()
    key = key_path()
    pub = key.with_suffix(".pub")
    configured = _git_get(repo.path, "user.signingKey")

    if check:
        from corkern.commands.signing_check import run_check
        run_check(repo.path)
        return

    if show:
        typer.echo(f"  Key           : {key} ({'exists' if key.is_file() else 'missing'})")
        if pub.is_file():
            from corkern.commands.trust_cmd import fingerprint
            words = guard.read_text(pub).split()
            typer.echo(f"  Fingerprint   : {fingerprint(repo.path, f'{words[0]} {words[1]}')}"
                       "  (compare it when another device asks: corkern trust)")
        typer.echo(f"  Repo signs with: {configured or '(global git config)'}")
        typer.echo(f"  Allowed signers: {repo.path / SIGNERS}")
        return

    ssh_keygen = guard.which("ssh-keygen")
    if not ssh_keygen:
        fail("ssh-keygen not found (install openssh-client).")
    if configured == str(key) and key.is_file():
        typer.echo(f"  ✓ Already set up: the knowledge repo signs with {key}")
    else:
        typer.echo("  corkern signing will:")
        if not key.is_file():
            typer.echo(f"    • create a new ed25519 key without passphrase: {key}")
        else:
            typer.echo(f"    • use the existing key: {key}")
        typer.echo(f"    • configure {repo.path} (repo-local git config) to sign commits with it")
        typer.echo(f"    • add its public key to {SIGNERS} (synced to your other machines)")
        typer.echo("    Your global git config and other repos are not changed.")
        if not _ask("  Proceed? [y/N] ", yes):
            _explain_setup_later(repo.path, key, ssh_keygen)
            return
    if not key.is_file():
        host = key.name.removeprefix("corkern_signing_")
        result = guard.capture_tool(Path.home(), [ssh_keygen, "-q", "-t", "ed25519", "-N", "",
                                                  "-C", f"corkern-signing@{host}", "-f", str(key)], "")
        if result.returncode != 0 or not key.is_file():
            fail(f"ssh-keygen could not create {key}")
        typer.echo(f"  ✓ Created signing key {key}")
    else:
        typer.echo(f"  ✓ Using existing signing key {key}")

    for name, value in (("gpg.format", "ssh"), ("gpg.ssh.program", ssh_keygen),
                        ("user.signingKey", str(key)), ("commit.gpgsign", "true"),
                        ("gpg.ssh.allowedSignersFile", str(repo.path / SIGNERS))):
        guard.run_git(repo.path, "config", "--local", name, value)
    typer.echo("  ✓ Knowledge repo signs with this machine's key (global git config untouched)")

    email = _git_get(repo.path, "user.email") or f"corkern@{key.name}"
    entry = f'{email} namespaces="git" {guard.read_text(pub).strip()}\n'
    signers = repo.path / SIGNERS
    existing = guard.read_text(signers) if signers.is_file() else ""
    if guard.read_text(pub).strip().split()[1] not in existing:
        guard.append_text(signers, entry)
    attributes = repo.path / ".gitattributes"
    rule = f"{SIGNERS} merge=union\n"
    if rule not in (guard.read_text(attributes) if attributes.is_file() else ""):
        guard.append_text(attributes, rule)

    # Self-test: a signed commit that verifies against allowed_signers
    guard.run_git(repo.path, "add", "--", SIGNERS, ".gitattributes")
    staged = guard.run_git(repo.path, "diff", "--cached", "--name-only").stdout.strip()
    if staged:
        guard.run_git(repo.path, "commit", "-q", "-m", f"corkern: signing key for {key.name}")
        try:
            guard.run_git(repo.path, "verify-commit", "HEAD")
            typer.echo("  ✓ Signed test commit verified")
        except guard.GitError as e:
            fail(f"Signed commit did not verify: {e}")
    else:
        typer.echo("  ✓ Public key already listed in allowed_signers")

    gh = guard.which("gh")
    if not gh:
        if register_github:
            fail("gh (GitHub CLI) not found — cannot register the key on GitHub.")
        _explain_github_later(pub, key, gh_available=False)
    elif _ask("  Also add this key to your GitHub account as a signing key, so these commits "
              "show as Verified? [y/N] ", register_github):
        if guard.run_tool(Path.home(), [gh, "ssh-key", "add", str(pub), "--type", "signing",
                                        "--title", f"corkern-signing {device_name()}"]) != 0:
            fail("gh ssh-key add failed (needs the admin:ssh_signing_key scope: "
                 "gh auth refresh -s admin:ssh_signing_key)")
        typer.echo("  ✓ Registered on GitHub as a signing key (commits show as Verified)")
    else:
        _explain_github_later(pub, key, gh_available=True)

    _push_key_step(repo.path, ssh_keygen, gh, deploy_key)


def _push_key_step(repo: Path, ssh_keygen: str, gh: str | None, preapproved: bool) -> None:
    """Per-machine push key: a deploy key with write access to THIS repo only."""
    remote = _git_get(repo, "remote.origin.url")
    match = GITHUB_REMOTE.search(remote or "")
    if not match:
        return                                   # no GitHub remote: nothing to push with
    slug = match.group(1)
    key = push_key_path()
    pub = key.with_suffix(".pub")
    ssh_cmd = f"ssh -i {key} -o IdentitiesOnly=yes -o IdentityAgent=none"
    if _git_get(repo, "core.sshCommand") == ssh_cmd and key.is_file():
        typer.echo(f"  ✓ Push key already set up ({key})")
        return
    typer.echo(f"""
  Push key — so corkern can push memories without your SSH agent / 1Password:
    • {'create' if not key.is_file() else 'use the existing'} key {key}
    • add it to github.com/{slug} as a deploy key with write access (this repo ONLY)
    • make this repo push with it (repo-local core.sshCommand, remote over SSH)""")
    if not _ask("  Set up the push key? [y/N] ", preapproved):
        _explain_push_later(repo, slug, key, ssh_cmd)
        return
    if not key.is_file():
        result = guard.capture_tool(Path.home(), [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-C",
                                                  f"corkern-push@{device_name()}", "-f", str(key)], "")
        if result.returncode != 0 or not key.is_file():
            fail(f"ssh-keygen could not create {key}")
        typer.echo(f"  ✓ Created push key {key}")
    if not gh:
        typer.echo("  gh not found — add the deploy key on GitHub by hand:")
        _explain_push_later(repo, slug, key, ssh_cmd, created=True)
        return
    if guard.run_tool(Path.home(), [gh, "repo", "deploy-key", "add", str(pub), "--allow-write",
                                    "--title", f"corkern-push {device_name()}", "-R", slug]) != 0:
        # a rerun after an earlier attempt: GitHub refuses the same key twice
        listed = guard.capture_tool(Path.home(), [gh, "repo", "deploy-key", "list", "-R", slug,
                                                  "--json", "key", "-q", ".[].key"], "", quiet=True)
        if " ".join(guard.read_text(pub).split()[:2]) not in (listed.stdout or ""):
            fail(f"gh repo deploy-key add failed (you need admin rights on {slug})")
        typer.echo(f"  ✓ Deploy key 'corkern-push {device_name()}' is already on {slug}")
    else:
        typer.echo(f"  ✓ Deploy key 'corkern-push {device_name()}' added to {slug} (write access)")
    # try SSH with the push key BEFORE switching: if it does not work (port 22 blocked, …) the
    # repo keeps pushing the way it does now
    ssh_url = f"git@github.com:{slug}.git"
    try:
        guard.run_git(repo, "-c", f"core.sshCommand={ssh_cmd}", "ls-remote", "--quiet", ssh_url, "HEAD", timeout=30)
    except guard.GitError as e:
        typer.echo(f"  ! github.com not reachable over SSH with the push key — nothing switched, this repo "
                   f"keeps pushing over {remote}\n    ({str(e).splitlines()[-1]})\n"
                   "    Try again later (e.g. on another network): corkern signing --deploy-key")
        return
    guard.run_git(repo, "config", "--local", "core.sshCommand", ssh_cmd)
    guard.run_git(repo, "remote", "set-url", "origin", ssh_url)
    typer.echo("  ✓ Remote reachable with the push key — this repo now pushes with it")
    typer.echo("  Check everything any time with: corkern signing --check")


def _explain_push_later(repo: Path, slug: str, key: Path, ssh_cmd: str, created: bool = False) -> None:
    head = "" if created else f"""  No push key set up — pushes use your normal SSH setup; if that needs approval
  (e.g. 1Password), background pushes wait or fail and are retried later.
  To set it up later, run:   corkern signing
  Or by hand:
    ssh-keygen -t ed25519 -N "" -C "corkern-push@{device_name()}" -f {key}
"""
    typer.echo(f"""{head}    github.com/{slug} → Settings → Deploy keys → Add deploy key
        title: corkern-push {device_name()} · key: contents of {key}.pub · tick "Allow write access"
    git -C {repo} config --local core.sshCommand "{ssh_cmd}"
    git -C {repo} remote set-url origin git@github.com:{slug}.git
  Then check: corkern signing --check""")


def _explain_setup_later(repo: Path, key: Path, ssh_keygen: str) -> None:
    host = key.name.removeprefix("corkern_signing_")
    signers = repo / SIGNERS
    typer.echo(f"""  Nothing changed.
  Without a machine key, background hook commits use your global git signing — if that
  asks for approval (e.g. 1Password), each commit waits for you or fails and is retried later.
  To set it up later, run:   corkern signing
  Or by hand:
    ssh-keygen -t ed25519 -N "" -C "corkern-signing@{host}" -f {key}
    git -C {repo} config --local gpg.format ssh
    git -C {repo} config --local gpg.ssh.program {ssh_keygen}
    git -C {repo} config --local user.signingKey {key}
    git -C {repo} config --local commit.gpgsign true
    git -C {repo} config --local gpg.ssh.allowedSignersFile {signers}
    echo "$(git -C {repo} config user.email) namespaces=\\"git\\" $(cat {key}.pub)" >> {signers}""")


def _explain_github_later(pub: Path, key: Path, gh_available: bool) -> None:
    typer.echo("  Not added to GitHub — these commits are signed and verified locally, but GitHub")
    typer.echo("  shows them as \"Unverified\" until the key is added there. To add it later:")
    if gh_available:
        typer.echo(f"    gh ssh-key add {pub} --type signing --title {key.name}")
        typer.echo("    (needs: gh auth refresh -s admin:ssh_signing_key)")
        typer.echo("  or on github.com:")
    typer.echo("    Settings → SSH and GPG keys → New SSH key → Key type: \"Signing Key\" → paste:")
    typer.echo(f"    {guard.read_text(pub).strip()}")


def _git_get(repo: Path, name: str) -> str:
    try:
        return guard.run_git(repo, "config", "--get", name).stdout.strip()
    except guard.GitError:
        return ""
