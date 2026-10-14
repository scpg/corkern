"""init-device — set this machine up so Claude Code reads and writes the knowledge repo.

  corkern init-device           show what would change (changes nothing)
  corkern init-device --apply   do it (asks first; --yes approves)

1. Projects: `autoMemoryDirectory` in each project's `.claude/settings.local.json` (local scope —
   machine-specific, kept out of the project's git), set to `<knowledge repo>/memory/<slug>`.
   Works however the project path is spelled on a machine; other keys are kept. Claude Code
   honours it once the folder is trusted. (v1 used symlinks under ~/.claude/projects/.)
2. Hooks: SessionStart / PostToolUse (Write|Edit) / Stop / SessionEnd in Claude Code's user
   `settings.json` — merged: other settings and hooks stay, an older corkern hook is replaced,
   never doubled. The previous file is kept as settings.json.bak-corkern-<time>.
   Machine-neutral, for dotfiles shared between machines: paths are written as $HOME/…, a hook
   does nothing on a machine without corkern, and the knowledge repo is not in settings.json —
   this machine's corkern (written by install.sh) knows it.
3. Global instructions: an `@~/<knowledge repo>/global/CLAUDE.md` import line in the user
   `CLAUDE.md` (your own content stays; Claude Code loads user-scope imports without asking).
4. Slash commands: /corkern-distill, /corkern-harvest, /corkern-memory-prune, /corkern-status
   (commands/corkern-*.md in Claude Code's folder; v1 had the same ones).

Claude Code's user files live in $CLAUDE_CONFIG_DIR, default ~/.claude. While corkern v1 is
installed on this machine, --apply is refused and v1's files are not touched (guard.py).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import typer

from corkern import __version__, devices, guard, hooks_install
from corkern.commands.reminders import fail
from corkern.commands.signing_cmd import _ask
from corkern.config import load_config

SETTINGS = Path(".claude") / "settings.local.json"
KEY = "autoMemoryDirectory"
HOOK_EVENTS = {"SessionStart": "corkern-session-start", "PostToolUse": "corkern-commit-memory",
               "Stop": "corkern-push", "SessionEnd": "corkern-push"}


@dataclass
class Step:
    slug: str
    project: Path
    target: Path
    action: str              # "set", "ok", "skip"
    note: str = ""
    settings: dict | None = None
    exclude: bool = False    # add .claude/settings.local.json to the project's .git/info/exclude
    home: bool = False       # the project at ~: written as Claude Code's ~/.claude/settings.local.json


@dataclass
class ClaudeFile:
    name: str                # settings.json / CLAUDE.md
    action: str              # "set", "ok", "skip"
    note: str = ""
    content: str = ""


def _v1_installed() -> bool:
    return guard.v1_installed()


def _code_dir() -> Path | None:
    """Where corkern's packaged files (hooks/, claude_commands/) are: the installed package."""
    return guard.package_dir() if (hooks_install.packaged("hooks") / "corkern.sh").is_file() else None


def _hooks_entry() -> Path:
    """The hook entry point as named in settings.json: the stable copy, not the package."""
    return hooks_install.entry()


def _home_relative(path: Path, home_word: str) -> str:
    """`path` as `<home_word>/…` when it is inside $HOME — the same text on every machine."""
    try:
        return f"{home_word}/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)


def hook_command(entry: Path, name: str) -> str:
    """The hook as written into settings.json: $HOME-relative, a no-op where corkern is absent."""
    return f'f="{_home_relative(entry, "$HOME")}"; [ -f "$f" ] || exit 0; bash "$f" {name}'


def _ignored(project: Path) -> bool:
    try:
        guard.run_git(project, "check-ignore", "-q", str(SETTINGS))
        return True
    except guard.GitError:
        return False


# ── 1. projects ─────────────────────────────────────────────────────────────────────────
def plan(repo, projects: dict[str, Path]) -> list[Step]:
    steps = []
    for slug, project in sorted(projects.items()):
        target = repo.memory_dir / slug
        if project == Path.home():
            # a project at ~ (v1's "home"): its local settings file is ~/.claude/settings.local.json,
            # which Claude Code reads only for sessions started in ~ (tested with real Claude Code)
            steps.append(_home_step(slug, project, target))
            continue
        try:
            guard.assert_safe(project)
        except guard.V1ProtectedError:
            steps.append(Step(slug, project, target, "skip", "v1-protected location"))
            continue
        if not project.is_dir():
            steps.append(Step(slug, project, target, "skip",
                              "not on this machine (set its path in corkern.conf.local)"))
            continue
        settings_file = project / SETTINGS
        current: dict = {}
        if settings_file.is_file():
            try:
                current = json.loads(guard.read_text(settings_file) or "{}")
            except json.JSONDecodeError:
                steps.append(Step(slug, project, target, "skip", f"{settings_file} is not valid JSON"))
                continue
            if not isinstance(current, dict):
                steps.append(Step(slug, project, target, "skip", f"{settings_file} is not a JSON object"))
                continue
        exclude = (project / ".git").exists() and not _ignored(project)
        if current.get(KEY) == str(target) and not exclude:
            steps.append(Step(slug, project, target, "ok"))
            continue
        was = current.get(KEY)
        steps.append(Step(slug, project, target, "set", f"(was: {was})" if was else "",
                          {**current, KEY: str(target)}, exclude))
    return steps


def _home_step(slug: str, project: Path, target: Path) -> Step:
    if guard.claude_config_dir() != Path(os.path.abspath(project / ".claude")):
        return Step(slug, project, target, "skip", "CLAUDE_CONFIG_DIR is set — the home project is not wired")
    try:
        text = guard.read_claude_config("settings.local.json")
    except guard.V1ProtectedError as e:
        return Step(slug, project, target, "skip", str(e).removeprefix("ERROR: ").split("\n")[0])
    try:
        current = json.loads(text) if text and text.strip() else {}
    except json.JSONDecodeError:
        return Step(slug, project, target, "skip", "~/.claude/settings.local.json is not valid JSON")
    if not isinstance(current, dict):
        return Step(slug, project, target, "skip", "~/.claude/settings.local.json is not a JSON object")
    if current.get(KEY) == str(target):
        return Step(slug, project, target, "ok")
    was = current.get(KEY)
    return Step(slug, project, target, "set",
                ("(was: " + was + ") " if was else "") + "in ~/.claude/settings.local.json — this machine only, "
                "keep it out of shared dotfiles", {**current, KEY: str(target)}, home=True)


def _apply(step: Step) -> None:
    if step.home:
        guard.write_claude_config("settings.local.json", json.dumps(step.settings, indent=2, ensure_ascii=False) + "\n")
        return
    guard.write_text(step.project / SETTINGS, json.dumps(step.settings, indent=2, ensure_ascii=False) + "\n")
    if step.exclude:
        exclude = step.project / ".git" / "info" / "exclude"
        text = guard.read_text(exclude) if exclude.is_file() else ""
        if str(SETTINGS) not in text.split():
            guard.append_text(exclude, ("" if not text or text.endswith("\n") else "\n") + f"{SETTINGS}\n")


# ── 2. hooks in settings.json ───────────────────────────────────────────────────────────
def merged_settings(current: dict, kr: Path, entry: Path) -> tuple[dict, list[str]]:
    """`current` with corkern's env + hooks; other settings and hooks untouched."""
    new = json.loads(json.dumps(current))
    notes = []
    env = new.get("env")
    if isinstance(env, dict) and env.get("CORKERN_MEMORY_DIR") == str(kr):   # written by an older corkern
        del env["CORKERN_MEMORY_DIR"]
        if not env:
            del new["env"]
        notes.append("env CORKERN_MEMORY_DIR removed (this machine's corkern knows the repo)")
    hooks = new.setdefault("hooks", {})
    for event, name in HOOK_EVENTS.items():
        ours = {"hooks": [{"type": "command", "command": hook_command(entry, name)}]}
        if event == "PostToolUse":
            ours = {"matcher": "Write|Edit", **ours}
        kept, replaced = [], 0
        for group in hooks.get(event, []):
            others = [h for h in group.get("hooks", []) if "corkern.sh" not in str(h.get("command", ""))]
            replaced += len(group.get("hooks", [])) - len(others)
            if others:
                kept.append({**group, "hooks": others})
        if ours not in hooks.get(event, []):
            notes.append(f"{event} → {name}" + (" (replaces an older corkern hook)" if replaced else ""))
        hooks[event] = kept + [ours]
    return new, notes


def _settings_plan(kr: Path) -> ClaudeFile:
    try:
        text = guard.read_claude_config("settings.json")
    except guard.V1ProtectedError as e:
        return ClaudeFile("settings.json", "skip", str(e).removeprefix("ERROR: ").split("\n")[0])
    try:
        current = json.loads(text) if text and text.strip() else {}
    except json.JSONDecodeError:
        return ClaudeFile("settings.json", "skip", "not valid JSON — fix it first, nothing changed")
    if not isinstance(current, dict):
        return ClaudeFile("settings.json", "skip", "not a JSON object — nothing changed")
    new, notes = merged_settings(current, kr, _hooks_entry())
    if new == current:
        return ClaudeFile("settings.json", "ok")
    return ClaudeFile("settings.json", "set", "; ".join(notes), json.dumps(new, indent=2, ensure_ascii=False) + "\n")


# ── 3. global CLAUDE.md ─────────────────────────────────────────────────────────────────
def _claude_md_plan(kr: Path) -> ClaudeFile:
    source = kr / "global" / "CLAUDE.md"
    if not source.is_file():
        return ClaudeFile("CLAUDE.md", "skip", "no global/CLAUDE.md in the knowledge repo")
    line = "@" + "\\ ".join(_home_relative(source, "~").split(" "))   # spaces need a backslash
    try:
        current = guard.read_claude_config("CLAUDE.md")
    except guard.V1ProtectedError as e:
        return ClaudeFile("CLAUDE.md", "skip", str(e).removeprefix("ERROR: ").split("\n")[0])
    if current and line in current.splitlines():
        return ClaudeFile("CLAUDE.md", "ok")
    absolute = "@" + "\\ ".join(str(source).split(" "))                # written by an older corkern
    if current and absolute != line and absolute in current.splitlines():
        content = "\n".join(line if x == absolute else x for x in current.split("\n"))
        return ClaudeFile("CLAUDE.md", "set", f"import line now {line} (same on every machine)", content)
    block = f"# corkern: global instructions from the knowledge repo (synced)\n{line}\n"
    content = (current.rstrip("\n") + "\n\n" if current and current.strip() else "") + block
    return ClaudeFile("CLAUDE.md", "set", f"import {source}" + (" (your content stays)" if current else ""),
                      content)


# ── 4. slash commands ───────────────────────────────────────────────────────────────────
def _commands_plan() -> list[ClaudeFile]:
    folder = hooks_install.packaged("claude_commands")
    sources = sorted(folder / name for name, _ in guard.list_files(folder, ".md")
                     if name.startswith("corkern-")) if folder.is_dir() else []
    plans = []
    for src in sources:
        name = f"commands/{src.name}"
        try:
            current = guard.read_claude_config(name)
        except guard.V1ProtectedError as e:
            plans.append(ClaudeFile(name, "skip", str(e).removeprefix("ERROR: ").split("\n")[0]))
            continue
        content = guard.read_text(src)
        plans.append(ClaudeFile(name, "ok") if current == content else
                     ClaudeFile(name, "set", f"/{src.stem}" + (" (updated)" if current else ""), content))
    return plans


def init_device(
    apply: bool = typer.Option(False, "--apply", help="Write the settings (asks first)"),
    yes: bool = typer.Option(False, "--yes", help="Approve without asking"),
) -> None:
    """Set this machine up: project memory, hooks and global instructions for Claude Code."""
    repo, projects = load_config()
    steps = plan(repo, projects)
    claude = [_settings_plan(repo.path), _claude_md_plan(repo.path), *_commands_plan()]
    cdir = guard.claude_config_dir()

    typer.echo(f"Projects: Claude Code memory → {repo.memory_dir}/<slug>  ({KEY} in {SETTINGS})")
    if not steps:
        typer.echo("  (no projects yet — add one with: corkern add <slug> [path])")
    for s in steps:
        if s.action == "ok":
            typer.echo(f"  ✓ {s.slug:<18} already set")
        elif s.action == "skip":
            typer.echo(f"  - {s.slug:<18} skipped: {s.note}")
        else:
            typer.echo(f"  → {s.slug:<18} {s.project / SETTINGS}: {KEY} = {s.target} {s.note}".rstrip())
            if s.exclude:
                typer.echo(f"    {'':<18} + {SETTINGS} added to {s.project}/.git/info/exclude "
                           "(machine-specific, never committed)")
    hooks_state = hooks_install.state()
    hooks_line = {"current": f"✓ hooks {hooks_install.home() / 'hooks'} (corkern {__version__})",
                  "missing": f"→ hooks: install corkern {__version__}'s hooks to {hooks_install.home() / 'hooks'}",
                  "outdated": f"→ hooks: update {hooks_install.home() / 'hooks'} "
                              f"({hooks_install.installed_version()} → {__version__})",
                  "git-clone": f"- hooks: {hooks_install.home()} is an older git-clone install — left as it is; "
                               f"to switch: mv {hooks_install.home()} {hooks_install.home()}.old-clone"}[hooks_state]
    typer.echo(f"\nHook scripts:\n  {hooks_line}")
    typer.echo(f"\nClaude Code ({cdir}):")
    for c in claude:
        mark = {"ok": "✓", "skip": "-", "set": "→"}[c.action]
        what = {"ok": "already set", "skip": f"skipped: {c.note}", "set": c.note}[c.action]
        typer.echo(f"  {mark} {c.name:<30} {what}")
    typer.echo("\n  Memories Claude Code already keeps in ~/.claude/projects/ are not moved "
               "(v1 memories come in with corkern migrate).")

    todo = [s for s in steps if s.action == "set"]
    todo_claude = [c for c in claude if c.action == "set"]
    hooks_todo = hooks_state in ("missing", "outdated")
    if not todo and not todo_claude and not hooks_todo:
        typer.echo("\n  ✓ Nothing to change.")
        return
    if not apply:
        typer.echo(f"\n  Nothing changed. To apply the {len(todo) + len(todo_claude) + hooks_todo} change(s): "
                   "corkern init-device --apply")
        return
    if _v1_installed():
        fail("corkern v1 is installed on this machine: pointing Claude's memory and hooks at v2 would\n"
             "  take them away from v1. Apply this only when switching to v2 (after UAT).")
    if not _ask(f"\n  Apply {len(todo) + len(todo_claude) + hooks_todo} change(s)? [y/N] ", yes):
        typer.echo("  Nothing changed.")
        return
    if hooks_todo:
        typer.echo(f"  ✓ hooks in {hooks_install.install()}")
    for s in todo:
        _apply(s)
        typer.echo(f"  ✓ {s.slug}")
    for c in todo_claude:
        backup = guard.write_claude_config(c.name, c.content)
        typer.echo(f"  ✓ {cdir / c.name}" + (f"  (previous version: {backup.name})" if backup else ""))
    devices.update(repo, projects)
    typer.echo("\n  Done. Takes effect in each project's next Claude Code session (trust the folder when\n"
               "  Claude Code asks). Check: /memory and /hooks in a session, corkern status here.")


def install_hooks() -> None:
    """Install (or update) corkern's hook scripts for Claude Code on this machine."""
    try:
        where = hooks_install.install()
    except hooks_install.HooksError as e:
        fail(str(e))
    typer.echo(f"✓ corkern {__version__} hooks in {where}")
