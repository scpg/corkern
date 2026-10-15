"""trust — which devices' memories this machine accepts (pull-time signature verification).

  corkern trust                       state: trusted devices, new devices, held-back commits
  corkern trust --enable              turn verification on (asks about each listed device)
  corkern trust --device SHA256:...   trust one more device, after checking its fingerprint
  corkern trust --revoke SHA256:...   stop trusting a device
  corkern trust --accept <sha>|all    integrate held-back commits after reviewing them
  corkern trust --disable             turn verification off on this machine

Every device signs its commits with its own key (`corkern signing`); the public keys are
listed in the synced `.devices/allowed_signers`. That list is a directory, not a trust
decision: anyone who can push could add a key to it. Each machine therefore keeps its OWN
trust store in `.git/corkern/trusted_signers` (never synced), filled only by the user
confirming a fingerprint here. With a trust store present, the hooks integrate an incoming
commit only if a trusted key signed it; everything else waits, listed in `.git/corkern/held`.
The same list holds commits whose content this machine never checks out (symlinks, files named
like corkern's state, Claude Code configuration, a .gitignore that re-includes this machine's own
files) — those are fixed on the remote, not accepted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import typer

from corkern import guard
from corkern.commands.reminders import fail
from corkern.commands.signing_cmd import SIGNERS, _ask
from corkern.config import load_config, repo_state_dir
from corkern.knowledge_repo import shown

HELD = "held"                 # in repo_state_dir: "<sha>\t<reason>" per line (hooks/lib.sh ck_hold)
UNTRUSTED = "not signed by a device this machine trusts"
ERROR = "ERROR"               # sentinel line: the pull could not inspect the incoming commits
COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


@dataclass(frozen=True)
class Signer:
    principal: str
    key: str          # "<type> <base64>"
    comment: str

    def line(self) -> str:
        return f'{self.principal} namespaces="git" {self.key}' + (f" {self.comment}" if self.comment else "")


def trust_file(repo: Path) -> Path:
    return repo_state_dir(repo) / "trusted_signers"


def accepted_file(repo: Path) -> Path:
    return repo_state_dir(repo) / "accepted"


def _held_lines(repo: Path) -> list[tuple[str, str]]:
    out = []
    for line in (guard.read_shared(repo_state_dir(repo) / HELD) or "").splitlines():
        sha, _, reason = line.partition("\t")
        if sha.strip():
            out.append((sha.strip(), reason.strip() or UNTRUSTED))
    return out


def held_entries(repo: Path) -> list[tuple[str, str]]:
    """(commit id or ERROR, reason) for everything the last pull held back. Entries are data:
    anything that is not a full commit id (or the ERROR sentinel) is dropped — see held_malformed."""
    return [(sha, reason) for sha, reason in _held_lines(repo) if sha == ERROR or COMMIT_ID.fullmatch(sha)]


def held_malformed(repo: Path) -> int:
    """How many lines of the held list were ignored (not a commit id)."""
    return sum(1 for sha, _ in _held_lines(repo) if not (sha == ERROR or COMMIT_ID.fullmatch(sha)))


def parse_signers(text: str) -> list[Signer]:
    signers = []
    for raw in text.splitlines():
        words = raw.split()
        if len(words) < 3 or raw.lstrip().startswith("#"):
            continue
        at = next((i for i, w in enumerate(words) if w.startswith(("ssh-", "ecdsa-", "sk-"))), None)
        if at is None or at + 1 >= len(words):
            continue
        signers.append(Signer(words[0], f"{words[at]} {words[at + 1]}", " ".join(words[at + 2:])))
    return signers


def _read_signers(path: Path) -> list[Signer]:
    return parse_signers(guard.read_text(path)) if path.is_file() else []


def _listed(repo: Path) -> list[Signer]:
    """Keys in .devices/allowed_signers — here AND on the remote (fetched, maybe held back).

    A new device announces its key in a commit that is itself held back until the key is
    trusted, so its fingerprint has to be found in the remote's version too. Listing is not
    trusting: a key is only trusted after the user confirms its fingerprint."""
    signers = _read_signers(repo / SIGNERS)
    upstream = _git(repo, "show", f"@{{upstream}}:{SIGNERS}")
    known = {s.key for s in signers}
    return signers + [s for s in parse_signers(upstream) if s.key not in known]


def fingerprint(repo: Path, key: str) -> str:
    ssh_keygen = guard.which("ssh-keygen")
    if not ssh_keygen:
        fail("ssh-keygen not found (install openssh-client).")
    result = guard.capture_tool(repo, [ssh_keygen, "-lf", "-"], key + "\n")
    words = (result.stdout or "").split()
    return words[1] if result.returncode == 0 and len(words) > 1 else "(unreadable key)"


def _git(repo: Path, *args: str) -> str:
    try:
        return guard.run_git(repo, *args).stdout.strip()
    except guard.GitError:
        return ""


def _own_signer(repo: Path) -> Signer | None:
    key_path = _git(repo, "config", "--local", "--get", "user.signingKey")
    pub = Path(key_path + ".pub") if key_path else None
    if not pub or not pub.is_file():
        return None
    words = guard.read_text(pub).split()
    if len(words) < 2:
        return None
    email = _git(repo, "config", "--get", "user.email") or "corkern"
    return Signer(email, f"{words[0]} {words[1]}", " ".join(words[2:]))


def _held(repo: Path) -> list[str]:
    """Held-back commits `--accept` can integrate: only those held for their signature."""
    return list(dict.fromkeys(sha for sha, reason in held_entries(repo) if reason == UNTRUSTED))


def _signature_kind(repo: Path, sha: str) -> str | None:
    """"SSH", "OpenPGP", "X.509" (or another kind) from the commit's gpgsig header; None if unsigned."""
    raw = _git(repo, "show", "-s", "--format=raw", "--end-of-options", sha, "--")
    header = next((line for line in raw.splitlines() if line.startswith("gpgsig")), None)
    if header is None:
        return None
    return "SSH" if "SSH SIGNATURE" in header else "OpenPGP" if "PGP SIGNATURE" in header \
        else "X.509" if "SIGNED MESSAGE" in header else "an unknown kind of"


def _describe_commit(repo: Path, sha: str, trusted: dict[str, str], claimed: dict[str, str]) -> list[str]:
    """Two lines about a held commit. `trusted`: fingerprint → name from this machine's trust store;
    `claimed`: fingerprint → name some device wrote into the synced allowed_signers (unverified)."""
    head = _git(repo, "log", "-1", "--date=short", "--format=%h  %ad  %an  %s", "--end-of-options", sha, "--")
    # only SSH, only against this machine's trust store (same settings as hooks/lib.sh)
    signed = _git(repo, "-c", f"gpg.ssh.allowedSignersFile={trust_file(repo)}", "-c", "gpg.ssh.program=ssh-keygen",
                  "-c", "gpg.openpgp.program=false", "-c", "gpg.x509.program=false",
                  "log", "-1", "--format=%G?|%GK|%P", "--end-of-options", sha, "--")
    status, key_fp, parents = (signed.split("|") + ["", "", ""])[:3]
    kind = _signature_kind(repo, sha)
    if kind is None:
        who = "unsigned"
    elif kind != "SSH":
        who = f"signed with {kind} signature — not an SSH signature, never accepted"
    elif status == "G" and key_fp in trusted:
        who = f"signed by {key_fp} ({trusted[key_fp]})"
    else:
        label = f"{claimed[key_fp]} (claimed, unverified)" if key_fp in claimed else "no name"
        who = f"signed by {key_fp or 'an unreadable key'} ({label}) — unknown key, not one of your trusted devices"
    n = len(parents.split())
    what = "root commit; " if n == 0 else f"merge of {n} parents; " if n > 1 else ""
    # -m --root: a merge against each parent, a root commit against nothing — what it really changes
    files = list(dict.fromkeys(_git(repo, "diff-tree", "-r", "--root", "-m", "--name-only", "--no-commit-id",
                                    "--end-of-options", sha, "--").splitlines()))
    shown = ", ".join(files[:4]) + (f" … +{len(files) - 4}" if len(files) > 4 else "")
    return [f"    {head}", f"      {what}{who}; files: {shown or 'none (same tree as its parent)'}"]


def _show(repo: Path) -> None:
    trusted = _read_signers(trust_file(repo))
    listed = _listed(repo)
    own = _own_signer(repo)
    fps = {s.key: fingerprint(repo, s.key) for s in {*trusted, *listed, *([own] if own else [])}}
    # names only from this machine's trust store; what the synced listing says is a claim
    names = {fps[s.key]: s.comment or s.principal for s in trusted}
    claimed = {fps[s.key]: s.comment or s.principal for s in listed if fps[s.key] not in names}

    if not trust_file(repo).is_file():
        typer.echo("Pull verification: OFF — memories from any device that can push are integrated\n"
                   "  (content this machine never checks out — symlinks, .claude/ … — is held back regardless).")
        typer.echo("  Turn it on with: corkern trust --enable")
    else:
        typer.echo(f"Pull verification: ON — only commits signed by these {len(trusted)} key(s) are integrated:")
        for s in trusted:
            mine = "  ← this machine" if own and s.key == own.key else ""
            typer.echo(f"  ✓ {fps[s.key]}  {s.comment or s.principal}{mine}")
    new = [s for s in listed if s.key not in {t.key for t in trusted}
           and not (own and s.key == own.key)]
    if new and trust_file(repo).is_file():
        typer.echo("\nListed in .devices/allowed_signers but NOT trusted on this machine:")
        for s in new:
            typer.echo(f"  ? {fps[s.key]}  {s.comment or s.principal}")
        typer.echo("  Compare the fingerprint with `corkern signing --show` on that device, then:\n"
                   "    corkern trust --device <fingerprint>")
    entries = held_entries(repo)
    errors = [reason for sha, reason in entries if sha == "ERROR"]
    content = [(sha, reason) for sha, reason in entries if sha != "ERROR" and reason != UNTRUSTED]
    if errors:
        typer.echo(f"\nNothing integrated from the remote: {errors[0]}.\n"
                   f"  Check: git -C {repo} fetch && git -C {repo} status — the next session start retries.")
    if content:
        typer.echo(f"\nHeld back from the remote ({len(content)}): content this machine never checks out:")
        for sha, reason in content:
            typer.echo(f"  ✗ {shown(reason)}")
            for row in _describe_commit(repo, sha, names, claimed):
                typer.echo(row)
        if any(reason.startswith("re-includes") for _, reason in content):
            typer.echo("  A .gitignore line starting with '!' that matches corkern.conf.local, .last-sync-*,\n"
                       "  .corkern.lock, *.bak-* or .distill-prompt.md would make git stage this machine's own\n"
                       "  files (a .gitignore wins over .git/info/exclude): remove that line on the remote.")
        typer.echo("  These cannot be accepted. Remove them on the remote (git rm <path>, commit, push) —\n"
                   "  everything else is integrated then — and find out who pushed them.")
    held = _held(repo)
    if held:
        typer.echo(f"\nHeld back from the remote ({len(held)} commit(s), not signed by a trusted device):")
        for sha in held:
            for row in _describe_commit(repo, sha, names, claimed):
                typer.echo(row)
        typer.echo("  If they are fine: corkern trust --accept all   (or trust their device: --device)\n"
                   "  If not: leave them — nothing from them reaches this machine's memory. Until they\n"
                   "  are gone from the remote, this machine keeps its new memories locally (committed,\n"
                   "  not pushed). To clean up: remove those commits from the remote and revoke the key\n"
                   "  that pushed them (GitHub: Settings → Deploy keys / collaborators).")
    elif trust_file(repo).is_file() and not entries:
        typer.echo("\n  ✓ Nothing held back.")
    bad = held_malformed(repo)
    if bad:
        typer.echo(f"\n  ! {bad} malformed entry ignored in {repo_state_dir(repo) / HELD} (not a commit id) — "
                   "the next pull rewrites the list")


def _add_trusted(repo: Path, signer: Signer) -> None:
    existing = _read_signers(trust_file(repo))
    if any(s.key == signer.key for s in existing):
        return
    guard.append_text(trust_file(repo), signer.line() + "\n")


def _next_sync_note() -> None:
    typer.echo("  They are integrated at the next session start or after Claude's next reply.")


def trust(
    enable: bool = typer.Option(False, "--enable", help="Turn pull verification on"),
    device: str = typer.Option(None, "--device", metavar="FINGERPRINT", help="Trust one more device"),
    revoke: str = typer.Option(None, "--revoke", metavar="FINGERPRINT", help="Stop trusting a device"),
    accept: str = typer.Option(None, "--accept", metavar="SHA|all", help="Integrate held-back commits"),
    disable: bool = typer.Option(False, "--disable", help="Turn pull verification off"),
    yes: bool = typer.Option(False, "--yes", help="Approve the questions without asking"),
) -> None:
    """Which devices' memories this machine accepts (signed-commit verification on pull)."""
    repo, _ = load_config()
    kr = repo.path

    if enable:
        own = _own_signer(kr)
        if not own:
            fail("This machine has no signing key yet — set it up first: corkern signing")
        _add_trusted(kr, own)
        typer.echo(f"  ✓ Pull verification is ON. Trusted: this machine ({fingerprint(kr, own.key)})")
        for s in _listed(kr):
            if s.key == own.key or any(t.key == s.key for t in _read_signers(trust_file(kr))):
                continue
            fp = fingerprint(kr, s.key)
            typer.echo(f"\n  Listed device: {s.comment or s.principal}\n  Fingerprint  : {fp}")
            if _ask("  Is this one of your devices (fingerprint checked)? Trust it? [y/N] ", yes):
                _add_trusted(kr, s)
                typer.echo("  ✓ Trusted")
            else:
                typer.echo(f"  Not trusted — its commits will be held back. Later: corkern trust --device {fp}")
        return

    if device or revoke:
        target = device or revoke
        listed = _listed(kr) + _read_signers(trust_file(kr))
        match = next((s for s in listed if fingerprint(kr, s.key) == target), None)
        if not match:
            fail(f"No key with fingerprint {target} in {SIGNERS} or this machine's trust store.\n"
                 "  See the fingerprints with: corkern trust")
        if device:
            if not trust_file(kr).is_file():
                fail("Pull verification is off — turn it on first: corkern trust --enable")
            if _ask(f"  Trust {match.comment or match.principal} ({target})? [y/N] ", yes):
                _add_trusted(kr, match)
                typer.echo("  ✓ Trusted — its held-back commits pass verification now.")
                _next_sync_note()
            else:
                typer.echo("  Nothing changed.")
            return
        keep = [s for s in _read_signers(trust_file(kr)) if s.key != match.key]
        if not keep:
            fail("That is the only trusted key — use --disable to turn verification off instead.")
        guard.write_text(trust_file(kr), "".join(s.line() + "\n" for s in keep))
        typer.echo(f"  ✓ No longer trusted: {target}. New commits it signs will be held back.")
        return

    if accept:
        if accept != "all" and not re.fullmatch(r"[0-9a-f]{4,64}", accept):
            fail(f"{accept!r} is not a commit id — use a hex commit id (see: corkern trust) or 'all'.")
        held = _held(kr)
        if not held:
            fail("Nothing is held back.")
        chosen = held if accept == "all" else [s for s in held if s.startswith(accept)]
        if not chosen:
            fail(f"{accept} is not a held-back commit — see: corkern trust")
        typer.echo("  Accept these commits into this machine's memory?")
        for sha in chosen:
            for row in _describe_commit(kr, sha, {}, {}):
                typer.echo(row)
        if not _ask("  [y/N] ", yes):
            typer.echo("  Nothing changed.")
            return
        for sha in chosen:
            guard.append_text(accepted_file(kr), sha + "\n")
        typer.echo(f"  ✓ Accepted {len(chosen)} commit(s).")
        _next_sync_note()
        return

    if disable:
        if not trust_file(kr).is_file():
            typer.echo("  Pull verification is already off.")
            return
        if not _ask("  Turn pull verification off (memories from any device that can push are "
                    "integrated again)? [y/N] ", yes):
            typer.echo("  Nothing changed.")
            return
        guard.remove_file(trust_file(kr))
        guard.remove_file(accepted_file(kr))
        guard.remove_file(repo_state_dir(kr) / HELD)
        typer.echo("  ✓ Pull verification is OFF. Turn it on again with: corkern trust --enable")
        return

    _show(kr)
