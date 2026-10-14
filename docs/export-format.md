# corkern export format — version 1

The contract between `corkern export` (v1) and its readers: `corkern import` (v1, recovery)
and `corkern migrate` / `corkern migrate --verify` (v2). One file holds everything corkern
holds on one machine, so it can be kept as a backup, restored, or checked against a v2 knowledge
repo — and whatever is missing can be imported with the user's OK.

## File

`corkern-export-<host>-<YYYYmmdd-HHMMSS>.tar.gz`, mode 0600 (it holds private memories), gzip'd tar:

```
manifest.json
data/      corkern's data, from the working tree (committed AND uncommitted):
           memory/**, .devices/*, global/CLAUDE.md, projects.conf, .metrics.tsv
local/     machine-local corkern files git ignores: projects.local.conf, .iwe/config.toml
claude/    the Claude Code wiring corkern v1 made on this machine (not the rest of Claude Code):
           settings.json        a copy of ~/.claude/settings.json (hooks live there)
           links.tsv            every symlink into the v1 repo: <link>\t<target> (CLAUDE.md,
                                corkern.sh, ~/.local/bin/corkern, commands, projects/*/memory)
           mcp.json             MCP servers whose command points into the v1 repo (e.g. iwe),
                                from ~/.claude.json — never the rest of that file (account data)
transcripts/  only with --with-transcripts: ~/.claude/projects/<key>/*.jsonl
```

Excluded always: `.git/`, sync/lock state (`.last-sync-*`, `.corkern.lock`), `.distill-prompt.md`,
editor/OS noise, anything outside the paths above.

## manifest.json

```json
{
  "format": "corkern-export",
  "format_version": 1,
  "created": "2026-10-06T10:15:00+02:00",
  "tool": "corkern v1",
  "tool_commit": "<v1 code commit>",
  "host": "host-b",
  "user": "userb",
  "home": "/home/userb",
  "repo": {
    "path": "/home/userb/dev/corkern",
    "commit": "<HEAD>",
    "branch": "main",
    "remote": "git@github.com:scpg/corkern.git",
    "uncommitted": ["memory/webshop/x.md", "projects.conf"],
    "unpushed_commits": 0
  },
  "projects": [
    {"slug": "webshop", "path": "dev/projects/webshop", "resolved": "/home/userb/dev/projects/webshop",
     "source": "projects.conf"}
  ],
  "files": [
    {"path": "data/memory/webshop/MEMORY.md", "kind": "memory", "size": 812,
     "sha256": "…", "committed": true}
  ]
}
```

`kind` is one of `memory`, `registry` (projects.conf), `devices`, `global`, `metrics`, `local`,
`claude`, `transcript`. A reader refuses a file whose `format` is not `corkern-export`, whose
`format_version` is newer than it knows, or whose checksums do not match — before using anything.

## Import rules (every reader)

1. **Check first, change nothing.** `--check` (and the start of every import) verifies the
   checksums and reports each item as `identical`, `missing here`, `different`, or `only here`.
2. **Never overwrite.** Something already present and different is kept as it is unless the user
   chooses otherwise; the choices are *keep mine* (default), *take the export's*, *keep both*
   (the export's version is written next to it as `<name>.from-export-<host>-<date>.<ext>`).
   `MEMORY.md` can also be *combined* (lines from both).
3. **Ask.** Missing items are proposed per group (with the list) and imported only after a yes.
   Claude Code wiring is only proposed where nothing is configured yet; existing settings are
   never replaced — at most a missing corkern hook is added, after a yes, with a backup first.
4. **Never delete.** Files only on the importing side (`only here`) are left alone.
5. **Report.** At the end: how many items were identical, imported, kept, kept both, skipped.
   Running the import again is always safe: imported items now show as identical.

v2 specifics: `claude/` is v1's wiring and is reported, not imported (`corkern init-device` sets up
v2's own); `local/projects.local.conf` holds the *source* machine's paths and is offered only when
importing on that same machine.

## Written by v2 (`corkern export`)

Same format and version; `"tool": "corkern v2"`. `data/` holds every file the knowledge repo
tracks or would commit (`git ls-files -co --exclude-standard`, incl. `corkern.conf` and
`.corkern-format`), `local/` holds `corkern.conf.local` and this machine's trust decisions
(`trusted_signers`, `accepted`), `claude/` holds v2's wiring (settings.json, the user CLAUDE.md,
corkern's slash commands, `projects.json` = which projects point their memory at the repo). The
manifest adds `knowledge_repo_format`. Never exported: private keys (each machine creates its
own), secrets removed before commit.

## Safety rules for readers

An export is treated as untrusted input:

- Only the entries listed above are accepted (v1 reader: anything else refuses the whole file).
- Nothing that can run is ever taken from the file: hooks, links and MCP servers are rebuilt from
  the reader's own definitions and its own paths; the file only says which of them existed.
- No write goes through a symlink or lands outside the target repo.
- Configuration that can define commands (`.iwe/config.toml`) is imported only after a separate,
  explicit yes — never with `--yes`.

## Knowledge repo format

Every v2 knowledge repo carries `.corkern-format` (`knowledge_repo_format=1`). corkern and the
hooks read it first: a newer number than they know → refuse and ask to update corkern; an older
one → upgrade step by step with a backup (no upgrades exist yet — format 1 is the first).
