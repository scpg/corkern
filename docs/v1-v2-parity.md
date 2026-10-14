# v1 → v2 feature comparison

Every v1 command, flag, hook, setup step and helper, compared with v2 (2026-10-02, v1 = `main`
`corkern` script + `sync.sh`, v2 = `corkern-v2` branch). "Tested" means an automated comparison
against the real v1 script (`tests/test_parity_v1.py`) or a run on the user's real data.

Legend: ✅ same behaviour · ⬆️ same + improved · ↔️ intentionally different · 🕓 planned for
`init-device` / after UAT · ❌ not in v2 yet

## Commands

| v1 | v2 | State | Notes |
|---|---|---|---|
| `get-AIinstructions` / `ai` / `context` | same names | ⬆️ | Lists the new commands; SessionStart adds reminders, actionable files, held-back commits, removed secrets, index-limit boxes |
| `add [slug] [path]` | `add` | ✅ tested | Same registry rules (relative/empty = HOME, first line per slug wins, Windows lines skipped) |
| `remove <slug>` | `remove` | ↔️ | v1 deletes the symlink and asks to `rm -rf` the memory; v2 only removes the registry line(s) — memory is kept (delete it with git if wanted) |
| `list [-s COL]... [-c COLS]` | `list` | ✅ tested | Same columns, `-s`/`-c`, device manifests (v1 format). STATUS on this device = autoMemoryDirectory wiring (v1: symlink); long paths shown whole (v1 cut at 44) |
| `log [slug]` | `log` | ✅ tested | |
| `sessions [slug]` | `sessions` | ↔️ tested | v2 orders by file name (date), v1 by mtime — wrong after a clone |
| `remind` / `done` / `todos` / `todo` | same | ✅ tested | Sections (`## …`) and global numbering as v1 |
| `status [-v]` / `doctor` | `status [-v]` / `doctor` | ⬆️ | v2 adds signing, push key, trust, secrets, MEMORY.md limits, hook wiring, stuck rebase; `-v` = v1's doctor.sh checks (project wiring, hook scripts, CLI, slash commands, unsynced Claude memory folders, graph age) + IWE, with a problems/warnings summary |
| `metrics` | `metrics` | ✅ tested | |
| `distill [--auto]` | `distill` | ⬆️ | Keeps the previous synthesis if Claude fails; no `$BRAIN` crash; projects from the registry |
| `memory-prune [slug] [--dry-run]` | `memory-prune` | ✅ tested | Never deletes (archives) |
| `graphify [slug] [--install] [--full]` | `graphify` | ✅ | Tested with real graphify 0.8.44 |
| `crg [--install]` | `crg` | ✅ | Tested with real code-review-graph 2.3.9 |
| `install` / `sync` (`sync.sh`) | `install.sh` + `init-device` | ⬆️ | See "Setup on a machine" below |
| `harvest [slug] [--all] [-i]` | `harvest` | ⬆️ | Same flow + v1's `.harvest-manifest`; fixed: crash when choosing among several conversations, silent overwrite of existing memories, project folders with `.`/`_`; new memories are indexed in MEMORY.md; tested with real Claude in Docker |
| `refine [slug]` | `refine` | ⬆️ | Bulk file moved to `archive/` instead of deleted |
| `prune [slug]` | `prune` | ✅ tested | Same rows as v1 (SIZE = real size, v1: disk usage) |
| `help` | `--help` (every command) | ✅ | |
| — | `migrate`, `init`, `signing`, `trust`, `secrets`, `init-device` | new | |

## Hooks

| v1 | v2 | State | Notes |
|---|---|---|---|
| `corkern-pull.sh` (SessionStart) | `corkern.sh corkern-session-start` | ⬆️ | Commits first (Claude Code creates MEMORY.md before hooks run), no `--autostash`, never stuck mid-rebase, keeps both sides of a conflict, verifies signatures when trust is on |
| `corkern-commit-memory.sh` (PostToolUse) | same | ⬆️ | Includes new (untracked) files; secret scan before commit; background push never rewrites files |
| `corkern-push.sh` (Stop) | same | ⬆️ | No network call when idle; integrates at most every 10 min |
| `corkern-session-notify.sh` | same | ⬆️ | |
| `lock.sh`, `preflight.sh` | `hooks/lib.sh` | ✅ | |
| `*.ps1` (native Windows: `sync.ps1`, `doctor.ps1`, hooks) | — | ❌ not ported: v2 runs on Linux, macOS and WSL; both current devices (host-a, host-b) are Linux/WSL |

## Setup on a machine (`sync.sh`)

| v1 step | v2 | State |
|---|---|---|
| Symlink each project's `~/.claude/projects/<key>/memory` → repo | `init-device`: `autoMemoryDirectory` per project (verified with real Claude Code) | ⬆️ |
| Symlink `~/.claude/CLAUDE.md` → `global/CLAUDE.md` | `init-device`: `@import` line in the user CLAUDE.md (own content stays) — verified with real Claude Code | ⬆️ |
| Wire hooks into `~/.claude/settings.json` | `init-device`: merged (other settings/hooks kept, old corkern hooks replaced, backup) — refused while v1 is installed | ✅ |
| Install `corkern` (+ `corkern-trace`) into `~/.local/bin` | `install.sh` installs `corkern` | ✅ (`corkern-trace` ❌; `CORKERN_HOOK_LOG` traces hooks instead) |
| Slash commands `~/.claude/commands/corkern-*.md` (distill, harvest, memory-prune, status) | installed by `init-device` (`/corkern-status` verified with real Claude Code) | ✅ |
| Device manifest `.devices/<host>` (projects + last seen) | written by `init-device`, stamped at session start | ✅ |
| Per-machine paths `projects.local.conf` | `corkern.conf.local` (same format) | ✅ — not migrated (machine-local by design) |
| Back up a real memory dir before linking it | not needed (no symlinks) | ↔️ |

## Other v1 pieces

| v1 | v2 | State |
|---|---|---|
| IWE MCP server over the memory repo (`scripts/iwec-corkern.sh`, untracked `.iwe/`) — **in use on host-a** | `corkern iwe --setup` (config committed + synced) / `--install` (MCP server `iwe` → `corkern iwe-serve`) — verified with real iwe 0.24.2 + real Claude Code | ⬆️ (v1's server stays until the switch) |
| `doctor.sh` | `status -v` | ✅ |
| `harvest-extract.py` | inside `harvest` | ✅ |

## Migration

`corkern migrate BUNDLE TARGET` builds a new knowledge repo from a `git archive` of v1:
memory, `.devices`, `global/CLAUDE.md`, `projects.conf` → `corkern.conf`. Only **committed** v1
data is in the bundle — commit v1 first. Not migrated: `.metrics.tsv`, `projects.local.conf`,
slash commands, `.iwe/`.

## Rehearsal with the real v1 data (2026-10-02, Docker, local only)

A read-only export of the real v1 repo (10 projects, 220 memory files) went through the whole
"new laptop" journey in Docker with real Claude Code 2.1.285 — 21/21 checks:

- `install.sh --migrate v1-export.tar` on the old laptop (this mode was added after the first
  rehearsal showed there was no way to migrate on a fresh machine), published to a private remote
- v1 and v2 on the same real data: 31 of 34 outputs identical; the 3 differences are `sessions`
  ordering (v1 shows a June note as newest after the export, v2 the September one — intended fix)
- `secrets --scan --history` on the real memories: nothing found; all 9 MEMORY.md indexes within
  Claude Code's read limit
- new laptop: `install.sh --clone`, own signing key, `init-device --apply` (8 projects), hooks
  wired; both laptops trust each other's keys
- real Claude sessions on the new laptop: read the migrated webshop memory index and the migrated
  global reminders; wrote a new memory → committed, signed by the new laptop, pushed, verified and
  pulled by the old laptop

Found along the way:

- **Commit v1 before exporting**: the export only holds committed data. Today that leaves out the
  `corkern-v2` project line, 3 memory files and the device manifest.
- **The `corkern` project itself** (`~/dev/corkern`) is a v1-protected path, so `init-device`
  skips it while the v1 guard rules apply — on purpose until v1 is retired.
- **Windows paths** (`sd-theProductName` → `/mnt/c/...`) exist only on the WSL machine: set them in
  `corkern.conf.local` there; other machines skip them.
