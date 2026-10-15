"""Knowledge repo template and initialisation."""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

from corkern import guard
from corkern.config import CONF_FILE

# Per-machine files that must never be synced, whatever the synced .gitignore says: they go to
# .git/info/exclude (same list as hooks/lib.sh CK_EXCLUDES — tests check they match). A
# .gitignore in the work tree wins over that file, so staging decides (`stage`, ck_unstage_local).
MACHINE_LOCAL = ("corkern.conf.local", ".corkern.lock", ".last-sync-*", "*.bak-*", ".distill-prompt.md")
_PROBES = ("corkern.conf.local", ".corkern.lock", ".last-sync-pull", "x.bak-1", ".distill-prompt.md")


def is_machine_local(rel: str) -> bool:
    """`rel`'s file name is a per-machine name — any case, at any depth (hooks: ck_is_local)."""
    name = rel.rsplit("/", 1)[-1].lower()
    return any(fnmatchcase(name, p.lower()) for p in MACHINE_LOCAL)


def machine_local_pathspecs() -> list[str]:
    """Pathspecs for every per-machine name that no .gitignore can defeat."""
    return [f":(glob,icase)**/{p}" for p in MACHINE_LOCAL]


def reincluding_lines(gitignore: str) -> list[str]:
    """The `!` lines of a .gitignore that would re-include a per-machine name (hooks: ck_reincludes)."""
    found = []
    for line in gitignore.splitlines():
        line = line.rstrip("\r")
        pat = line[1:].rstrip("/").rsplit("/", 1)[-1].lower() if line.startswith("!") else ""
        if pat and (is_machine_local(pat) or any(fnmatchcase(probe, pat) for probe in _PROBES)):
            found.append(line)
    return found


def _born(repo: Path) -> bool:
    try:
        guard.run_git(repo, "rev-parse", "--verify", "-q", "HEAD")
        return True
    except guard.GitError:
        return False


def stage(repo: Path, *args: str) -> None:
    """`git add ARGS`, then never a per-machine file: unstaged right away, and if one is still
    staged, everything is unstaged and GitError is raised (nothing gets committed)."""
    guard.run_git(repo, "add", *args)
    specs = machine_local_pathspecs()
    born = _born(repo)
    if born:
        guard.run_git(repo, "reset", "-q", "--", *specs)
        staged = guard.run_git(repo, "diff", "--cached", "--name-only", "--no-renames", "-z").stdout
    else:                                     # first commit: everything in the index is staged
        guard.run_git(repo, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", *specs)
        staged = guard.run_git(repo, "ls-files", "-z").stdout
    bad = sorted(p for p in staged.split("\0") if p and is_machine_local(p))
    if bad:
        guard.run_git(repo, *(("reset", "-q") if born else ("rm", "-r", "-q", "--cached", ".")))
        raise guard.GitError(f"not committed: per-machine file(s) staged in {repo}: {', '.join(bad)}")


def tracked_machine_local(repo: Path) -> list[str]:
    """Per-machine files git tracks (committed by an older version or a teammate)."""
    try:
        out = guard.run_git(repo, "ls-files", "-z", "--", *machine_local_pathspecs()).stdout
    except guard.GitError:
        return []
    return sorted(p for p in out.split("\0") if p)


def gitignore_reincludes(repo: Path) -> list[tuple[str, str]]:
    """(.gitignore path, `!` line) for every line in the work tree that re-includes a per-machine name."""
    try:
        listed = guard.run_git(repo, "ls-files", "-z", "-co", "--exclude-standard", "--",
                               ":(glob,icase)**/.gitignore").stdout.split("\0")
    except guard.GitError:
        return []
    found = []
    for rel in sorted({p for p in listed if p}):
        text = guard.read_shared(repo / rel)
        found += [(rel, line) for line in reincluding_lines(text or "")]
    return found

# The knowledge repo's own format version. Every corkern reads it first: a newer one than it
# knows is refused ("update corkern"), an older one is upgraded step by step (none yet).
FORMAT_FILE = ".corkern-format"
FORMAT_VERSION = 1

TEMPLATE: dict[str, str] = {
    FORMAT_FILE: f"knowledge_repo_format={FORMAT_VERSION}\n",
    CONF_FILE: (
        "# corkern project registry — format: slug:path\n"
        "# Shared across devices. Per-machine overrides go in corkern.conf.local (gitignored).\n"
    ),
    ".gitignore": """\
# ── Corkern per-machine state (never synced) ─────────────────────────────────
corkern.conf.local
.corkern.lock
.last-sync-*
*.bak-*
.distill-prompt.md

# ── Keys / secrets ───────────────────────────────────────────────────────────
*.key
*.pem
*.p12
*.secret
*.token
.env*
secrets/
credentials/
id_rsa
id_dsa
id_ecdsa
id_ed25519
*.ppk
*.kdbx
.netrc
.npmrc
.pypirc
.git-credentials

# ── *.local dirs (machine-only scratch) ──────────────────────────────────────
**/*.local/*
!**/*.local/.gitkeep

# ── Logs / OS / editor noise ─────────────────────────────────────────────────
*.log
.DS_Store
Thumbs.db
*~
*.swp
*.bak
*.orig
*.rej
""",
    "README.md": """\
# corkern knowledge repo

Private memory store for [corkern](https://github.com/scpg/corkern). Keep this repo private.

    corkern.conf          slug:path registry (shared across devices)
    corkern.conf.local    per-machine path overrides (gitignored)
    memory/<slug>/        memory files per project
    memory/global/        cross-project memories
    .devices/<host>       device manifests
    global/CLAUDE.md      global Claude instructions
""",
    "memory/global/MEMORY.md": "# Global — Memory Index\n",
    # Memory indexes are line lists edited on several devices: merge them line-wise
    # instead of conflicting (the hooks' conflict fallback covers every other file).
    ".gitattributes": "memory/**/MEMORY.md merge=union\n.metrics.tsv merge=union\ncorkern.conf merge=union\n",
    ".devices/.gitkeep": "",
    "global/CLAUDE.md": (
        "# Global Claude Instructions\n\n"
        "<!-- Instructions here apply to every project on every device. -->\n"
    ),
}

INITIAL_COMMIT_MSG = "corkern: initialize knowledge repo"


class InitError(Exception):
    pass


def repo_symlinks(repo: Path) -> list[str]:
    """Repo-relative paths of symlinks in the knowledge repo (tracked, or untracked and not
    ignored). corkern never follows them and treats them as absent; status and session-context
    list them so the user can remove them."""
    try:
        staged = guard.run_git(repo, "ls-files", "-s", "-z").stdout
        untracked = guard.run_git(repo, "ls-files", "-o", "--exclude-standard", "-z").stdout
    except guard.GitError:
        return []
    tracked = {entry.split("\t", 1)[1] for entry in staged.split("\0") if entry.startswith("120000 ")}
    # still a link on disk (a manifest corkern replaced by a regular file is no longer one)
    return sorted(rel for rel in tracked | set(untracked.split("\0")) if rel and (repo / rel).is_symlink())


def harden(repo: Path) -> None:
    """Defence in depth for a knowledge repo (the hooks repeat it on every run): symlinks are
    checked out as plain files, per-machine files are excluded locally."""
    guard.run_git(repo, "config", "--local", "core.symlinks", "false")
    exclude = repo / ".git" / "info" / "exclude"
    have = (guard.read_shared(exclude) or "").splitlines()
    missing = [p for p in MACHINE_LOCAL if p not in have]
    if missing:
        guard.append_text(exclude, "".join(p + "\n" for p in missing))


def require_dir_or_absent(target: Path) -> None:
    """The target and its nearest existing ancestor must be directories (or not exist)."""
    for candidate in (target, *target.parents):
        if candidate.exists():
            if not candidate.is_dir():
                raise InitError(f"{candidate} exists and is not a directory.")
            return


def _has_commit(repo: Path) -> bool:
    # Check .git first: otherwise rev-parse would find an enclosing repo's HEAD
    if not (repo / ".git").exists():
        return False
    try:
        guard.run_git(repo, "rev-parse", "--verify", "-q", "HEAD")
        return True
    except guard.GitError:
        return False


def init_repo(path: Path) -> bool:
    """Create a knowledge repo at `path`. Returns False if one already exists there.

    Re-running after a failed attempt (e.g. no git identity) resumes: missing template
    files are added, existing files are left as they are, then the initial commit is made.
    """
    target = guard.assert_safe(path)
    require_dir_or_absent(target)
    has_conf = (target / CONF_FILE).is_file()
    if has_conf and _has_commit(target):
        return False
    if not has_conf and not guard.is_empty(target):
        raise InitError(f"{target} is not empty and is not a corkern knowledge repo.")

    for rel, content in TEMPLATE.items():
        if not (target / rel).exists():
            guard.write_text(target / rel, content)
    if not (target / ".git").exists():
        guard.run_git(target, "init", "-q", "-b", "main")
    harden(target)
    stage(target, "--", *TEMPLATE)
    guard.run_git(target, "commit", "-q", "-m", INITIAL_COMMIT_MSG)
    return True
