"""corkern's Claude Code hooks, installed from the package to a stable, machine-neutral place.

Claude Code's settings.json names `$HOME/.local/share/corkern/hooks/corkern.sh` (the same text on
every machine, see init-device). The installed package lives somewhere version-specific (uv tool
or pipx environment), so the packaged hooks are copied there, with a VERSION file: `install()`
(called by `corkern init-device --apply`, `corkern install-hooks` and install.sh) and `state()`
(checked by `corkern status`).
"""
from __future__ import annotations

import os
from pathlib import Path

from corkern import __version__, guard

HOOK_FILES = ("corkern.sh", "lib.sh", "corkern-pull.sh", "corkern-push.sh",
              "corkern-commit-memory.sh", "corkern-session-notify.sh")


class HooksError(RuntimeError):
    pass


def home() -> Path:
    """Where the hooks are installed: $CORKERN_CODE_DIR (tests) or ~/.local/share/corkern."""
    return Path(os.environ.get("CORKERN_CODE_DIR") or Path.home() / ".local" / "share" / "corkern")


def entry() -> Path:
    return home() / "hooks" / "corkern.sh"


def packaged(folder: str) -> Path:
    """`hooks` or `claude_commands` inside the installed package."""
    return guard.package_dir() / folder


def installed_version() -> str | None:
    f = home() / "hooks" / "VERSION"
    return guard.read_text(f).strip() if f.is_file() else None


def state() -> str:
    """'current', 'outdated', 'missing' or 'git-clone' (an older install.sh layout)."""
    if (home() / ".git").exists():
        return "git-clone"
    if not all((home() / "hooks" / name).is_file() for name in HOOK_FILES):
        return "missing"
    return "current" if installed_version() == __version__ else "outdated"


def install() -> Path:
    """Copy the packaged hooks to home()/hooks (0755) and stamp VERSION. Returns the folder."""
    if (home() / ".git").exists():
        raise HooksError(f"{home()} is an older git-clone install of corkern — move it away first:\n"
                         f"  mv {home()} {home()}.old-clone")
    src, dest = packaged("hooks"), home() / "hooks"
    for name in HOOK_FILES:
        f = src / name
        if not f.is_file():
            raise HooksError(f"the installed corkern package has no hooks/{name} — reinstall corkern")
        guard.write_executable(dest / name, guard.read_bytes(f))
    guard.write_text(dest / "VERSION", __version__ + "\n")
    return dest
