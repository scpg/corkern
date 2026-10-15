> ⚠ **Test release — please do not use it yet.**
> This is one of corkern's first uploads to PyPI, published only to try the release process. It
> is not meant for use. A proper version is planned for the coming weeks.

# corkern

**Collective knowledge for Claude Code — across projects, devices and people.**

Claude Code keeps what it learns about you and your work in per-project memory files on one
machine. corkern turns that into a synced knowledge base: a private git repository that every
machine you use reads from and writes to, so what was learned in one project, on one laptop, is
there the next time — anywhere.

## Install — once per machine

```
uv tool install corkern        # or: pipx install corkern
corkern setup --clone git@github.com:<you>/<your-private-knowledge-repo>.git
corkern signing                # this machine's signing (+ push) key — asks first
corkern init-device --apply    # wire Claude Code — shows the plan, asks first
corkern doctor                 # check the installation
```

First machine without a knowledge repo yet: `corkern setup --create-github <you>/<name>` creates
it as a **private** GitHub repository (`--new` keeps it local).

corkern is meant to be installed exactly once per user, as a tool. A copy inside a project's
virtual environment refuses to act (only `--version`, `--help`, `doctor` and `status` run) —
`corkern doctor` shows every copy it finds.

## What it does

- **Syncs memories** between machines through a private git repository you own: pulled when a
  Claude session starts, committed as memories are written, pushed when Claude finishes a reply.
- **Brings context into every session**: open reminders, recent session notes and important
  memories are shown to Claude at session start.
- **Keeps concurrent edits side by side**: when two machines change the same memory, both versions
  are kept (`<name>.conflict-<host>-<time>.md`) and reported. Memory indexes and the project
  registry merge line by line.
- **Stays out of Claude's way**: the hooks always exit successfully; sync problems are recorded and
  shown by `corkern status`.
- **One key per machine**: each machine signs its memory commits with its own key and can push
  with a deploy key limited to the knowledge repository.
- **Accepts only what your devices signed** (opt-in, `corkern trust`): incoming memories signed by
  any other key, or unsigned, are held back until you accept them.
- **Keeps secrets out of the repository**: before every commit, known token formats, private keys
  and `password = …`-style values are replaced by a marker; the original stays on that machine.
  The scan is pattern-based: a secret in a format it does not know can still get through.
- **Warns when `MEMORY.md` outgrows what Claude Code loads** (200 lines / 25 KB).
- **Uses Claude Code's own setting** (`autoMemoryDirectory`) to point each project's memory at the
  knowledge repository.

## What corkern writes on your machine

| File | What corkern adds |
|---|---|
| `~/.config/corkern/machine.conf` | this machine's knowledge repository (machine-local) |
| `~/.local/share/corkern/hooks/` | its hook scripts, copied from the package |
| `~/.claude/settings.json` | one hook each for SessionStart, PostToolUse, Stop, SessionEnd — merged, a backup first |
| `~/.claude/CLAUDE.md` | one line: `@~/.corkern/global/CLAUDE.md` |
| `~/.claude/commands/corkern-*.md` | four slash commands |
| `<project>/.claude/settings.local.json` | `autoMemoryDirectory` (kept out of the project's git) |

What goes into `~/.claude` is the same on every machine (`$HOME`-relative, a no-op where corkern
is not installed), so it works with dotfile managers such as yadm, chezmoi or stow. A file that is
a symlink is never written through.

## Status

corkern was built by one developer for their own machines. Team use has only been simulated (10
Docker containers, three teams, one untrusted intruder). Use it at your own risk; when something
goes wrong, corkern reports it and keeps both versions rather than overwriting.

Memories leave the machine only through git, to the knowledge repository you configure — and,
when you run `corkern distill` or `corkern harvest`, to Claude for the analysis.

For Claude Code there is an open request for built-in sync of user config and memory:
[anthropics/claude-code#87027](https://github.com/anthropics/claude-code/issues/87027).

## Licence

MIT
