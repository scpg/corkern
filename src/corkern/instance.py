"""One corkern per machine: which copy is this, and what else is installed.

corkern must be installed once per user as a tool — `uv tool install corkern` or
`pipx install corkern` — and that central copy is the only one allowed to act. A copy in a
project virtualenv (`pip install corkern`, `uv run`) would wire hooks and write the knowledge repo
next to the central one, so it refuses every command except `--version` and `doctor`/`status`
(main.py). Python cannot stop such an install from happening (wheels run no code when installed);
it can stop the copy from acting.

Detection is by marker files, not paths: uv writes `uv-receipt.toml` into each tool environment,
pipx writes `pipx_metadata.json`. CORKERN_DEV=1 marks a development checkout (tests only).
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from corkern import __version__, guard


@dataclass
class Copy:
    path: Path              # the command on PATH, or the environment
    kind: str               # uv-tool, uv-tool-editable, pipx, other, v1
    note: str = ""


@dataclass
class Inventory:
    this: Copy
    copies: list[Copy] = field(default_factory=list)
    v1: list[str] = field(default_factory=list)
    old_clone: Path | None = None
    old_wrapper: Path | None = None
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _kind(prefix: Path) -> str:
    receipt = prefix / "uv-receipt.toml"
    if receipt.is_file():
        return "uv-tool-editable" if "editable" in guard.read_text(receipt) else "uv-tool"
    if (prefix / "pipx_metadata.json").is_file():
        return "pipx"
    return "other"


def this_copy() -> Copy:
    prefix = Path(sys.prefix)
    return Copy(prefix, _kind(prefix))


def is_central() -> bool:
    """True for a uv-tool / pipx install (editable ones included: a deliberate dev install)."""
    return os.environ.get("CORKERN_DEV") == "1" or this_copy().kind != "other"


def _env_of(command: Path) -> Path | None:
    """The Python environment behind an installed console script (its shebang's prefix)."""
    try:
        first = guard.read_bytes(command)[:300].split(b"\n", 1)[0].decode(errors="replace")
    except OSError:
        return None
    m = re.match(r"#!\s*(\S+)", first)
    if not m:
        return None
    python = Path(m.group(1))
    return python.parent.parent if python.parent.name == "bin" else None


def inventory() -> Inventory:
    inv = Inventory(this_copy())
    home = Path.home()
    for name in ("corkern", "corkern2"):
        for cmd in guard.which_all(name):
            target = cmd.resolve()
            if target.name == "corkern" and (target.parent / "projects.conf").exists():
                inv.v1.append(f"v1 command {cmd} → {target}")
                continue
            text = guard.read_bytes(cmd)[:2000].decode(errors="replace")
            if "CORKERN_MACHINE_REPO=" in text:
                inv.old_wrapper = cmd
                continue
            env = _env_of(cmd)
            inv.copies.append(Copy(cmd, _kind(env) if env else "other", str(env or "")))
    v1_repo = home / "dev" / "corkern"
    if (v1_repo / "projects.conf").is_file():
        inv.v1.append(f"v1 repo {v1_repo}")
    if (guard.claude_config_dir() / "corkern.sh").exists():
        inv.v1.append(f"v1 hook entry {guard.claude_config_dir() / 'corkern.sh'}")
    try:
        settings = guard.read_claude_config("settings.json") or ""
    except guard.V1ProtectedError:
        settings = ""
    if ".claude/corkern.sh" in settings:
        inv.v1.append("v1 hooks in Claude Code's settings.json")
    from corkern import hooks_install
    if hooks_install.state() == "git-clone":
        inv.old_clone = hooks_install.home()

    # verdict
    envs = {c.note for c in inv.copies if c.note}
    if inv.this.kind == "other" and os.environ.get("CORKERN_DEV") != "1":
        inv.problems.append(f"this corkern ({sys.prefix}) is not a tool install — it refuses to act. "
                            "Install corkern once with: uv tool install corkern (or pipx install corkern), "
                            f"and remove this copy (pip uninstall corkern in {sys.prefix})")
    if len(envs) > 1:
        inv.problems.append(f"{len(envs)} corkern installations found — keep exactly one: "
                            + ", ".join(sorted(envs)))
    if any(c.kind == "other" for c in inv.copies):
        inv.problems.append("a corkern command on PATH comes from a non-tool environment: "
                            + ", ".join(str(c.path) for c in inv.copies if c.kind == "other"))
    if inv.v1:
        inv.warnings.append("corkern v1 is still installed here (switch: migrate/3-switch-v1-machine.sh)")
    if inv.old_clone:
        inv.warnings.append(f"old git-clone install at {inv.old_clone} — move it away, then "
                            "corkern init-device --apply")
    if inv.old_wrapper:
        inv.warnings.append(f"old install.sh wrapper {inv.old_wrapper} — reinstall: uv tool install --force corkern")
    if any(c.kind == "uv-tool-editable" for c in inv.copies) or inv.this.kind == "uv-tool-editable":
        inv.warnings.append("a development (editable) install is active — fine on a dev machine, "
                            "replace it with the released package elsewhere")
    return inv


def describe(c: Copy) -> str:
    return {"uv-tool": "uv tool", "uv-tool-editable": "uv tool (editable, development)",
            "pipx": "pipx", "other": "NOT a tool install"}.get(c.kind, c.kind)


__all__ = ["Copy", "Inventory", "inventory", "is_central", "this_copy", "describe", "__version__"]
