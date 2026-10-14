"""The hooks and the pull itself never act through what a teammate pushed (audit H3 M4 L8 H5).

Threat: someone with push access to the shared knowledge repo commits symlinks, files named like
corkern's per-machine state, Claude Code configuration, or a file named like a revision range.
Each device here has its own scratch HOME and state dir; victim files live in the scratch dir.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.test_trust import Device, devs  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
C2 = ROOT / ".venv" / "bin" / "corkern"
ENTRY = ROOT / "src" / "corkern" / "hooks" / "corkern.sh"
ORIGINAL = "export PATH=$HOME/bin:$PATH\n"
TOKEN = "ghp_" + "Z" * 36


def git(repo, *args, check=False):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=check).stdout.strip()


class Machine:
    def __init__(self, tmp: Path, name: str, repo: Path):
        self.repo, self.home, self.state = repo, tmp / f"home-{name}", tmp / f"state-{name}"
        self.home.mkdir(exist_ok=True)
        self.env = {**os.environ, "HOME": str(self.home), "XDG_STATE_HOME": str(self.state),
                    "CORKERN_MEMORY_DIR": str(repo), "CORKERN_HOOK_FOREGROUND": "1",
                    "CORKERN_DEVICE_NAME": name, "PATH": f"{ROOT / '.venv' / 'bin'}:{os.environ['PATH']}"}

    def hook(self, name, timeout=60, **extra):
        return subprocess.run(["bash", str(ENTRY), name], input="{}", capture_output=True, text=True,
                              env={**self.env, **extra}, timeout=timeout)

    def c2(self, *args):
        return subprocess.run([str(C2), *args], capture_output=True, text=True, env=self.env, timeout=60)

    def write(self, rel, text):
        p = self.repo / "memory" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def held(self) -> str:
        f = self.repo / ".git" / "corkern" / "held"
        return f.read_text() if f.is_file() else ""


@pytest.fixture
def team(tmp_path):
    """bare remote, A = a victim device (initialised), E = a teammate with push access (raw git)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    a_repo = tmp_path / "A"
    subprocess.run([str(C2), "init", str(a_repo)], check=True, capture_output=True,
                   env={**os.environ, "HOME": str(tmp_path)})
    git(a_repo, "remote", "add", "origin", str(remote))
    git(a_repo, "push", "-q", "-u", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(remote), str(tmp_path / "E")], check=True)
    a = Machine(tmp_path, "A", a_repo)
    a.hook("corkern-pull")
    victims = {}
    for rel in (".bashrc", ".ssh/authorized_keys"):
        v = tmp_path / rel
        v.parent.mkdir(parents=True, exist_ok=True)
        v.write_text(ORIGINAL)
        victims[rel] = v
    return {"a": a, "e": tmp_path / "E", "remote": remote, "tmp": tmp_path, "victims": victims}


def push_raw(repo: Path, links: dict[str, str] = None, files: dict[str, str] = None, msg="teammate"):
    """What a teammate can do with plain git: force-add anything, push."""
    for rel, target in (links or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_symlink() or p.exists():
            p.unlink()
        p.symlink_to(target)
    for rel, text in (files or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    paths = [*(links or {}), *(files or {})]
    git(repo, "add", "-f", "--", *paths, check=True)
    git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", msg, check=True)
    git(repo, "pull", "-q", "--rebase")
    git(repo, "push", "-q", check=True)


def untouched(team) -> bool:
    return all(v.read_text() == ORIGINAL for v in team["victims"].values())


# ── H3: committed symlinks at corkern's state paths ──────────────────────────────────────
def test_h3_state_symlinks_are_held_and_never_written_through(team):
    a = team["a"]
    push_raw(team["e"], links={".corkern.lock": "../.bashrc", ".last-sync-pull": "../.ssh/authorized_keys"},
             files={"memory/p/e.md": "from E\n"})
    for name in ("corkern-pull", "corkern-pull", "corkern-push"):
        assert a.hook(name).returncode == 0
    assert untouched(team)
    assert not (a.repo / ".corkern.lock").is_symlink() and not (a.repo / ".last-sync-pull").is_symlink()
    assert not (a.repo / "memory/p/e.md").exists()          # the whole incoming range waits
    assert "symlink" in a.held() and ".corkern.lock" in a.held()
    status = a.c2("status").stdout
    assert "held back" in status and ".corkern.lock" in status
    context = subprocess.run([str(C2), "session-context"], input="{}", capture_output=True, text=True,
                             env=a.env, timeout=60).stdout
    assert "held back" in context and ".corkern.lock" in context


def test_h3_fifo_lock_never_blocks_the_hook(team):
    a, fifo = team["a"], team["tmp"] / "fifo"
    os.mkfifo(fifo)
    try:
        push_raw(team["e"], links={".corkern.lock": "../fifo"})
        for name in ("corkern-pull", "corkern-pull", "corkern-push"):
            try:
                assert a.hook(name, timeout=20).returncode == 0
            except subprocess.TimeoutExpired:
                pytest.fail(f"{name} blocked on a FIFO behind a committed .corkern.lock link")
    finally:
        fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)     # release a writer stuck on open()
        os.close(fd)


# ── M4: keep-both writes through a symlink at the conflicting path ───────────────────────
def test_m4_keep_both_never_writes_through_a_symlink(team):
    a, e = team["a"], team["e"]
    push_raw(e, files={"memory/p/same.md": "base\n"})
    a.hook("corkern-pull")
    assert (a.repo / "memory/p/same.md").read_text() == "base\n"
    # a link that reached this machine before (e.g. integrated by an older corkern)
    link = a.repo / "memory/p/same.md"
    link.unlink()
    link.symlink_to(team["victims"][".bashrc"])
    git(a.repo, "add", "-A", check=True)
    git(a.repo, "commit", "-qm", "local link", check=True)
    push_raw(e, files={"memory/p/same.md": "remote change\n"})
    for name in ("corkern-pull", "corkern-push"):
        assert a.hook(name).returncode == 0
    assert untouched(team)


# ── L8: per-machine state committed after a pushed .gitignore; credentials in error files ──
def test_l8_pushed_gitignore_never_makes_state_synced(team):
    a, e = team["a"], team["e"]
    stripped = "\n".join(line for line in (e / ".gitignore").read_text().splitlines()
                         if not line.startswith((".last-sync", ".corkern.lock", "corkern.conf.local")))
    push_raw(e, files={".gitignore": stripped + "\n"})
    (a.repo / "corkern.conf.local").write_text("web:/srv/private\n")
    a.hook("corkern-pull")
    a.write("p/x.md", "x\n")
    a.hook("corkern-commit-memory")
    a.hook("corkern-push")
    a.write("p/y.md", "y\n")
    a.hook("corkern-push")
    tree = git(team["remote"], "ls-tree", "-r", "--name-only", "main")
    assert "memory/p/y.md" in tree
    assert not [p for p in tree.splitlines() if ".last-sync" in p or ".corkern.lock" in p
                or p == "corkern.conf.local"], tree


def test_l8_remote_credentials_never_written_or_printed(team):
    a = team["a"]
    git(a.repo, "remote", "set-url", "origin", f"https://corkern:{TOKEN}@127.0.0.1:9/kr.git", check=True)
    a.write("p/x.md", "x\n")
    a.hook("corkern-commit-memory")
    a.hook("corkern-push")
    leaked = [str(f) for base in (a.repo, a.state, a.home) for f in base.rglob("*")
              if f.is_file() and not f.is_symlink() and f != a.repo / ".git" / "config"
              and "/objects/" not in str(f) and TOKEN in f.read_text(errors="replace")]
    assert not leaked, leaked
    assert list((a.repo / ".git" / "corkern").glob("last-sync-push-error"))     # the error is recorded
    assert TOKEN not in a.c2("status").stdout


# ── H5: a file named like a revision range must not switch verification off ──────────────
def test_h5_planted_revision_named_file_does_not_bypass_verification(devs):  # noqa: F811
    """Step 1: the file reaches A while nothing is verified (trust still off). Step 2: with trust
    on, `rev-list HEAD..@{upstream}` became ambiguous (a file of that name is in the work tree),
    failed, and an empty held list let every untrusted commit in."""
    a, e = devs["A"], devs["E"]
    e.hook("corkern-pull")
    (e.repo / "HEAD..@{upstream}").write_text("x\n")
    e.sync()
    a.hook("corkern-pull")
    assert (a.repo / "HEAD..@{upstream}").is_file()                 # planted in A's work tree
    a.c2("trust", "--enable", "--yes")
    e.write("p/evil.md", "from an untrusted device\n")
    e.sync()
    a.hook("corkern-pull")
    assert not (a.repo / "memory/p/evil.md").exists(), "untrusted commit integrated"
    held = (a.repo / ".git" / "corkern" / "held").read_text()
    assert git(devs["remote"], "rev-parse", "main") in held


# ── pull holds commits that add links, state names, Claude Code configuration ────────────
@pytest.mark.parametrize("change", [
    {"files": {".claude/settings.json": '{"hooks": {}}\n'}},
    {"files": {".mcp.json": '{"mcpServers": {}}\n'}},
    {"files": {"memory/p/.last-sync-pull": "x\n"}},
    {"links": {"memory/p/link.md": "../../../.bashrc"}},
    {"links": {"memory/p/inside.md": "other.md"}},
])
def test_dangerous_incoming_content_is_held(team, change):
    a = team["a"]
    push_raw(team["e"], links=change.get("links"), files={**change.get("files", {}), "memory/p/ok.md": "ok\n"})
    a.hook("corkern-pull")
    assert not (a.repo / "memory/p/ok.md").exists()
    rel = next(iter({**change.get("files", {}), **change.get("links", {})}))
    assert rel in a.held()
    assert untouched(team)


def test_held_content_is_integrated_once_the_remote_removes_it(team):
    a, e = team["a"], team["e"]
    push_raw(e, links={"memory/p/link.md": "../../../.bashrc"}, files={"memory/p/ok.md": "ok\n"})
    a.hook("corkern-pull")
    assert not (a.repo / "memory/p/ok.md").exists()
    git(e, "rm", "-q", "memory/p/link.md", check=True)
    git(e, "commit", "-qm", "remove link", check=True)
    git(e, "push", "-q", check=True)
    a.hook("corkern-pull")
    assert (a.repo / "memory/p/ok.md").read_text() == "ok\n"
    assert a.held() == ""


def test_pull_fails_closed_when_inspection_fails(team):
    a = team["a"]
    shim = team["tmp"] / "shim"
    shim.mkdir()
    real_git = subprocess.run(["bash", "-c", "command -v git"], capture_output=True, text=True).stdout.strip()
    (shim / "git").write_text(f'#!/bin/bash\nfor x in "$@"; do [ "$x" = rev-list ] && exit 1; done\n'
                              f'exec {real_git} "$@"\n')
    (shim / "git").chmod(0o755)
    push_raw(team["e"], files={"memory/p/ok.md": "ok\n"})
    a.hook("corkern-pull", PATH=f"{shim}:{a.env['PATH']}")
    assert not (a.repo / "memory/p/ok.md").exists()
    assert "could not inspect" in a.held()


# ── per-machine state: out of the work tree; core.symlinks=false ─────────────────────────
def test_state_moves_into_the_git_dir_without_following_links(team):
    a = team["a"]
    for name in (".last-sync-pull", ".last-sync-conflicts"):
        (a.repo / name).write_text("2026-01-01 00:00:00\n")
    (a.repo / ".last-sync-untrusted").write_text("abc123\n")
    (a.repo / ".corkern.lock").write_text("")
    (a.repo / ".last-sync-push").symlink_to(team["victims"][".bashrc"])
    a.hook("corkern-pull")
    state = a.repo / ".git" / "corkern"
    assert (state / "last-sync-conflicts").read_text() == "2026-01-01 00:00:00\n"
    for name in (".last-sync-pull", ".last-sync-conflicts", ".last-sync-untrusted", ".corkern.lock"):
        assert not (a.repo / name).exists(), name
    assert untouched(team)
    exclude = (a.repo / ".git" / "info" / "exclude").read_text()
    assert ".last-sync-*" in exclude and "corkern.conf.local" in exclude
    assert (state / "last-sync-pull").is_file() and not (state / "last-sync-pull").is_symlink()


def test_core_symlinks_is_off_everywhere(team, tmp_path):
    a = team["a"]
    assert git(a.repo, "config", "--local", "--get", "core.symlinks") == "false"     # init + hooks
    fresh = tmp_path / "fresh"
    subprocess.run(["git", "clone", "-q", str(team["remote"]), str(fresh)], check=True)
    Machine(tmp_path, "F", fresh).hook("corkern-commit-memory")
    assert git(fresh, "config", "--local", "--get", "core.symlinks") == "false"
    clone = tmp_path / "home-S" / "kr"
    r = subprocess.run([str(C2), "setup", "--clone", str(team["remote"]), "--memory-dir", str(clone)],
                       capture_output=True, text=True, timeout=120,
                       env={**Machine(tmp_path, "S", clone).env, "XDG_CONFIG_HOME": str(tmp_path / "cfg-S")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(clone, "config", "--local", "--get", "core.symlinks") == "false"


def test_a_link_that_slips_through_is_checked_out_as_a_plain_file(team):
    """Defence in depth: with core.symlinks=false git never creates a link in the work tree."""
    a = team["a"]
    a.hook("corkern-pull")
    push_raw(team["e"], links={"memory/p/l.md": "../../../.bashrc"})
    git(a.repo, "fetch", "-q", check=True)
    git(a.repo, "merge", "-q", "--ff-only", "origin/main", check=True)   # bypasses the hooks
    p = a.repo / "memory/p/l.md"
    assert not p.is_symlink() and stat.S_ISREG(p.lstat().st_mode)


def test_hook_excludes_match_python():
    import re
    import shlex
    from corkern.knowledge_repo import MACHINE_LOCAL
    lib = (ROOT / "src" / "corkern" / "hooks" / "lib.sh").read_text()
    bash = shlex.split(re.search(r"^CK_EXCLUDES=\((.*)\)$", lib, re.M).group(1))
    assert tuple(bash) == MACHINE_LOCAL
