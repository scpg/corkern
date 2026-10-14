"""Knowledge repo template and initialisation."""
from __future__ import annotations

from pathlib import Path

from corkern import guard
from corkern.config import CONF_FILE

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
    guard.run_git(target, "add", "--", *TEMPLATE)
    guard.run_git(target, "commit", "-q", "-m", INITIAL_COMMIT_MSG)
    return True
