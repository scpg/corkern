"""corkern signing --check — verify this machine's signing + push setup, locally and on GitHub.

Read-only. Works after the manual ("show me how") or the automatic route. Every failed check
says how to fix it. GitHub checks need `gh` (logged in, or GH_TOKEN); without it they are
listed as not checked, with what to look at by hand.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import typer

from corkern import guard

SIGNERS = ".devices/allowed_signers"
GITHUB_REMOTE = re.compile(r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?$")


class Check:
    def __init__(self) -> None:
        self.failed = 0
        self.warned = 0

    def ok(self, msg: str) -> None:
        typer.echo(f"  ✓ {msg}")

    def fail(self, msg: str, fix: str) -> None:
        typer.echo(f"  ✗ {msg}\n      fix: {fix}")
        self.failed += 1

    def warn(self, msg: str, hint: str = "") -> None:
        typer.echo(f"  ! {msg}" + (f"\n      {hint}" if hint else ""))
        self.warned += 1

    def section(self, title: str) -> None:
        typer.echo(f"\n{title}")


def _git(repo: Path, *args: str, timeout: float | None = None) -> str | None:
    try:
        return guard.run_git(repo, *args, timeout=timeout).stdout.strip()
    except guard.GitError:
        return None


def _key_blob(pub: Path) -> str | None:
    if not pub.is_file():
        return None
    parts = guard.read_text(pub).split()
    return parts[1] if len(parts) >= 2 else None


def _push_key(repo: Path) -> Path | None:
    cmd = _git(repo, "config", "--local", "--get", "core.sshCommand") or ""
    words = shlex.split(cmd) if cmd else []
    if "-i" in words and words.index("-i") + 1 < len(words):
        return Path(words[words.index("-i") + 1]).expanduser()
    return None


def _gh_json(gh: str, repo: Path, *args: str):
    result = guard.capture_tool(repo, [gh, "api", *args], "")
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        return None


def _local(c: Check, repo: Path) -> tuple[Path | None, Path | None]:
    c.section("This machine")
    key_cfg = _git(repo, "config", "--local", "--get", "user.signingKey")
    sign_key = Path(key_cfg) if key_cfg else None
    if not sign_key:
        c.fail("Knowledge repo has no machine signing key configured", "corkern signing")
    elif not sign_key.is_file():
        c.fail(f"Signing key {sign_key} is missing", "corkern signing")
        sign_key = None
    else:
        mode = sign_key.stat().st_mode & 0o777
        (c.ok if mode & 0o077 == 0 else lambda m: c.fail(m, f"chmod 600 {sign_key}"))(
            f"Signing key {sign_key} (permissions {mode:o})")
    for name, want in (("gpg.format", "ssh"), ("commit.gpgsign", "true")):
        got = _git(repo, "config", "--local", "--get", name)
        if got != want:
            c.fail(f"{name} is {got!r}, expected {want!r}", f"git -C {repo} config --local {name} {want}")
    program = _git(repo, "config", "--get", "gpg.ssh.program") or ""
    if "op-ssh-sign" in program:
        c.fail("Signing goes through 1Password (interactive)",
               f"git -C {repo} config --local gpg.ssh.program {guard.which('ssh-keygen') or 'ssh-keygen'}")
    if sign_key:
        blob = _key_blob(sign_key.with_suffix(".pub"))
        signers = repo / SIGNERS
        if blob and signers.is_file() and blob in guard.read_text(signers):
            c.ok(f"Public key listed in {SIGNERS}")
        else:
            c.fail(f"Public key not in {SIGNERS}", "corkern signing (adds it)")
        if _git(repo, "verify-commit", "HEAD") is not None:
            c.ok("Latest commit verifies against allowed_signers")
        else:
            c.warn("Latest commit does not verify locally (older or another machine's commit?)",
                   f"git -C {repo} log -1 --show-signature")

    c.section("Pushing")
    remote = _git(repo, "remote", "get-url", "origin")
    push_key = _push_key(repo)
    if not remote:
        c.warn("No remote configured — memories stay on this machine")
        return sign_key, push_key
    c.ok(f"Remote: {remote}")
    ssh_cmd = _git(repo, "config", "--local", "--get", "core.sshCommand") or ""
    if not push_key:
        c.warn("No per-machine push key — pushes use your normal SSH setup (may need 1Password approval)",
               "set up a deploy key for this repo (corkern signing explains how)")
    elif not push_key.is_file():
        c.fail(f"Push key {push_key} is missing", f"recreate it or remove core.sshCommand from {repo}")
        push_key = None
    else:
        c.ok(f"Push key {push_key}")
        if "IdentityAgent=none" not in ssh_cmd or "IdentitiesOnly=yes" not in ssh_cmd:
            c.warn("Push key may still be bypassed by an SSH agent (1Password)",
                   "add -o IdentitiesOnly=yes -o IdentityAgent=none to core.sshCommand")
    if remote.startswith("https://"):
        c.warn("Remote uses HTTPS — the per-machine push key is only used with an SSH remote",
               f"git -C {repo} remote set-url origin git@github.com:<owner>/<repo>.git")
    if _git(repo, "ls-remote", "--quiet", "origin", "HEAD", timeout=20) is not None:
        c.ok("Remote reachable with this setup (read access)")
    else:
        c.fail("Cannot reach the remote with this setup",
               "check the deploy key on GitHub (below) and the network")
    return sign_key, push_key


def _github(c: Check, repo: Path, sign_key: Path | None, push_key: Path | None) -> None:
    c.section("GitHub")
    remote = _git(repo, "remote", "get-url", "origin") or ""
    match = GITHUB_REMOTE.search(remote)
    if not match:
        c.warn("Remote is not on github.com — GitHub checks skipped")
        return
    slug = match.group(1)
    gh = guard.which("gh")
    if not gh or _gh_json(gh, repo, "user") is None:
        c.warn("gh not available or not logged in — GitHub side not checked",
               f"check by hand: github.com/{slug} → Settings → Deploy keys; "
               "your account → Settings → SSH and GPG keys → Signing keys")
        return
    account = _gh_json(gh, repo, "user")["login"]
    c.ok(f"gh logged in as {account}")

    keys = _gh_json(gh, repo, f"repos/{slug}/keys")
    push_blob = _key_blob(push_key.with_suffix(".pub")) if push_key else None
    if keys is None:
        c.warn(f"Cannot list deploy keys of {slug} (needs admin access to the repo)")
    elif push_blob:
        found = next((k for k in keys if push_blob in k.get("key", "")), None)
        if not found:
            c.fail("This machine's push key is not a deploy key of the repo",
                   f"github.com/{slug} → Settings → Deploy keys → Add deploy key → paste "
                   f"{push_key}.pub → tick 'Allow write access'")
        elif found.get("read_only"):
            c.fail(f"Deploy key '{found['title']}' is read-only — pushes will be rejected",
                   f"github.com/{slug} → Settings → Deploy keys → delete it and add it again "
                   "with 'Allow write access'")
        else:
            c.ok(f"Deploy key '{found['title']}' matches this machine and can write")

    sign_blob = _key_blob(sign_key.with_suffix(".pub")) if sign_key else None
    signing_keys = _gh_json(gh, repo, "user/ssh_signing_keys")
    if sign_blob:
        if signing_keys is None:
            c.warn("Cannot list your GitHub signing keys (gh needs the read:ssh_signing_key scope)",
                   "gh auth refresh -s read:ssh_signing_key — or check Settings → SSH and GPG keys")
        elif any(sign_blob in k.get("key", "") for k in signing_keys):
            c.ok(f"Signing key registered on GitHub account {account}")
        else:
            c.warn(f"Signing key not registered on GitHub account {account} — "
                   "commits are verified locally but show 'Unverified' on GitHub",
                   f"gh ssh-key add {sign_key}.pub --type signing "
                   "(or Settings → SSH and GPG keys → New SSH key → Key type: Signing Key)")

    head = _git(repo, "rev-parse", "HEAD")
    pushed = _git(repo, "branch", "-r", "--contains", head or "x")
    if head and pushed:
        commit = _gh_json(gh, repo, f"repos/{slug}/commits/{head}")
        verification = (commit or {}).get("commit", {}).get("verification", {})
        reason = verification.get("reason")
        if verification.get("verified"):
            c.ok(f"Latest pushed commit {head[:7]} shows as Verified on GitHub")
        elif reason == "unknown_key":
            c.warn(f"GitHub does not know the key that signed {head[:7]} (shows 'Unverified')",
                   "register this machine's signing key on the account (see above)")
        elif reason in ("bad_email", "unverified_email", "no_user"):
            email = _git(repo, "log", "-1", "--format=%ae")
            c.fail(f"GitHub cannot match the commit email {email} to the account ({reason})",
                   "commit with a verified email of the account, e.g. its "
                   "<id>+<user>@users.noreply.github.com address: "
                   f"git -C {repo} config --local user.email <that address>")
        elif reason:
            c.warn(f"Latest commit {head[:7]} is not Verified on GitHub ({reason})")


def run_check(repo: Path) -> None:
    c = Check()
    sign_key, push_key = _local(c, repo)
    _github(c, repo, sign_key, push_key)
    typer.echo("")
    if c.failed:
        typer.echo(f"  {c.failed} problem(s), {c.warned} warning(s) — see the fixes above.")
        raise typer.Exit(1)
    typer.echo(f"  ✓ All checks passed" + (f" ({c.warned} warning(s))" if c.warned else "") + ".")
