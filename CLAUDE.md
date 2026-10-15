# Corkern v2 — AI Instructions

This is the **v2 rewrite** of corkern, a memory sync system for Claude Code.
It is a standalone Python CLI, physically separate from the v1 bash repo (by default `~/dev/corkern/`).

- **This repo** — application code only, no memory files
- **Knowledge repo** (the user's, private; `$CORKERN_MEMORY_DIR`) — memories, registry, config

**About v1.** v1 is the original corkern: a set of bash scripts, never published, still in use on
the maintainer's machines. v2 (this repo) replaces it and is built to run beside it on the same
machine without ever touching it, which is why the code has a v1 guard, a v1 sentinel and a
`migrate` command. **Without v1 you can ignore all of that:** the v1 guard simply never triggers,
and the tests that need the v1 code (parity, migration, scenarios) skip or run locally only.
"Before switch-over" below means: on a machine that still runs v1, before v2 has replaced it there.

## Status

| Area | State |
|---|---|
| CLI | `init`, `migrate`, `list/ls`, `add`, `remove/rm`, `status/doctor` (health), `remind`, `done`, `todos/todo`, `sessions`, `log`, `get-AIinstructions/ai/context`, `graphify`, `crg`, `memory-prune`, `metrics`, `distill`, `signing`, `trust`, `secrets`, `init-device`, `iwe`, `harvest`, `refine`, `prune` |
| Hooks (`hooks/`, bash) | `corkern.sh corkern-session-start` (pull → context), `corkern-commit-memory`, `corkern-push`; wired by `init-device` |
| Installer | `install.sh` (`--new`, `--clone`, `--create-github`, existing); installs only `corkern` |
| Parity vs v1 | cases vs the REAL v1 script (`tests/test_parity_v1.py`, local only) |
| Safety | secret scan before every hook commit (redact + local quarantine, `secrets`); pull-time signature verification against a LOCAL trust store (`trust`): SSH only (OpenPGP/X.509 switched off), a commit counts only with `%G?` = G and its key in `.git/corkern/trusted_signers`, names only from that store; held entries must be commit ids and every revision goes to git after `--end-of-options`; import/migrate never write a `.git` component in any form (`guard.unsafe_member`, git's verify_path rules) and migrate commits only after the hooks' secret scan (fails closed); migrate/3's carry-over verifies its export point (hex, ancestor, = script 1's own record) before v1 is touched, writes only via temp + `mv -T` (no symlinks), scans before commit and before push; a slug's path is read (harvest/prune/graphify/crg) or wired (init-device) only when this machine confirmed it (`.git/corkern/projects-confirmed`: `add`, corkern.conf.local, a per-row y, migrate; a pushed corkern.conf.local is held), flagged in status/list/session context; migrate exits 1 when its scan cannot run (rerun resumes); MEMORY.md 200-line/25 KB warnings (status + session context) |
| `init-device` | per-project `autoMemoryDirectory`, hooks + env merged into the user `settings.json`, `@import` of global/CLAUDE.md, device manifest — verified with real Claude Code in Docker; `--apply` refused while v1 is installed (guard: `read/write_claude_config`, only those two files, never through a symlink) |
| v1 parity | every v1 command, flag, hook and setup step has a v2 equivalent except native-Windows `.ps1` — docs/v1-v2-parity.md |
| Release | `.github/workflows/release.yml` (tag `v*` → TestPyPI / PyPI, Trusted Publishing) — docs/releasing.md |

Never create keys or GitHub resources silently: ask, and when the user declines, show what to
do instead.

## Architecture decisions (already made — do not re-debate)

- **Language**: Python + Typer + uv (`uv venv --seed`, `.venv/bin/pytest`)
- **Command name**: `corkern` (renamed from `corkern2` on 2026-10-13 for the PyPI release; no alias).
  v1's `~/.local/bin/corkern` is a link into its repo — v2 refuses to install while it is there
  (install.sh, guard `is_v1_command`); migrate/3-switch-v1-machine.sh retires v1 first
- **Knowledge repo location**: `$CORKERN_MEMORY_DIR` (required env var, no fallback in the CLI). Each
  machine's repo is recorded outside `~/.claude`, in `~/.config/corkern/machine.conf` (`corkern setup`;
  env still wins; the older install.sh `corkern2` wrapper is read as a fallback), so `~/.claude` can be
  shared between machines (e.g. dotfiles): `$HOME` paths, `@~/` import, hooks no-op without corkern
- **GitHub**: confirmed best option for shared memory backend
- **Bash hooks**: stay as bash — only the CLI is being rewritten
- **Two repos**: code repo (this) + user's private knowledge repo (separate git repo)
- **Migration input**: a `git archive` export of v1 (`corkern migrate BUNDLE`) — v2 never reads v1

## Coexistence rules (CRITICAL on machines that still run v1)

v1 (`~/dev/corkern/`) and v2 can run on the same machine simultaneously. **Never break v1.**
These rules apply when developing on such a machine; without v1 only rule 1 matters.
1. `CORKERN_MEMORY_DIR` must point to a TEST knowledge repo during development
2. `~/dev/corkern/.corkern-v1.sentinel` is a physical tripwire
3. v2 hooks must never be wired into `~/.claude/settings.json` before switch-over
4. v2's `corkern` never replaces v1's `corkern` link: install.sh refuses while v1 is installed
5. Never run `install.sh` on such a machine before switch-over (sandbox / Docker only)
6. Read v1 only with `GIT_OPTIONAL_LOCKS=0` — a plain `git status` can rewrite v1's `.git/index`.
   Check v1 after work (memory/ changes are fine; anything else = red flag)

**v1 isolation guard** (`src/corkern/guard.py`) — the only module allowed to touch the
filesystem or start processes (enforced by `tests/test_guard_enforcement.py`). Refuses any path
that, itself or via any ancestor, as given or symlink-resolved: contains the sentinel, contains
`projects.conf` (v1 layout), is `~/dev/corkern` / `$CORKERN`, is under `~/.claude/`, or is
`~/.local/bin/corkern` (for both `$HOME` and the passwd home). External tools only via
`guard.run_tool/capture_tool/exec_tool` with an allow-list (graphify, claude, code-review-graph).
The hooks carry the same guard in bash (`hooks/lib.sh` `ck_is_v1`, parity-tested).
Inside the knowledge repo (registered by `MemoryRepo`) no path may go through a symlink below the
root (the root itself may be one): explicit actions raise `SymlinkInRepoError`, listing skips links,
`guard.read_shared` treats a symlinked shared file as absent; status/session-context list them.
`claude` never starts inside the knowledge repo (its `.claude/` is shared): `run/capture/exec_tool` refuse
it; text-only `claude -p` calls go through `claude_print.ask` (fresh private temp dir, `--setting-sources
user --strict-mcp-config --tools ""`), interactive sessions through `claude_print.interactive_cwd`.
Loosening a guard rule is a deliberate, reviewed change (e.g. `init-device` may write `~/.claude/`,
but only through `read/write_claude_config` and never while v1 is installed).

## Sync design (hard-won)

- Never blocks Claude (hooks always exit 0; only session context writes stdout), never loses text.
- Claude Code creates `memory/<slug>/MEMORY.md` BEFORE SessionStart hooks run → pull commits
  local changes first.
- Line-wise union merge (template `.gitattributes`, checked by `status`): `memory/**/MEMORY.md`,
  `corkern.conf`, `.metrics.tsv`. Other conflicts keep both: `<name>.conflict-<host>-<time>.md`.
- Never `--autostash` (strands writes in the stash). Fetch, commit all, rebase locally; anything
  that rewrites files on disk runs only in SessionStart/Stop (Claude idle); background is push-only.
- Stop integrates at most every 10 min and makes no network call when idle.
- Per-machine sync state (lock, `last-sync-*`, `held`, trust store) lives in `.git/corkern/`, never in
  the work tree; per-machine names (`CK_EXCLUDES` = `MACHINE_LOCAL`) go to `.git/info/exclude`, but a
  `.gitignore` wins over that file, so staging decides: after every `git add` (hooks, migrate, init,
  migrate/3) they are unstaged by `:(glob,icase)**/` pathspecs, a commit still holding one is refused,
  a tracked one is skip-worktree (status/session context: "tracked per-machine file"); `core.symlinks=false`.
  Pull resolves the upstream SHA once, inspects merge-base..SHA (symlinks, state names, `.claude/`,
  `.mcp.json`, a `.gitignore` whose `!` line re-includes a per-machine name → held) and fails closed;
  hooks never write to a work-tree path through a shell redirection.
- Empty remote: first push does `push -u origin HEAD`.

## Testing

| Command | What |
|---|---|
| `.venv/bin/pytest tests/ -q` | the unit suite; parity vs real v1 only with a `v1` remote (skips otherwise) |
| `make test-docker` | suite in a clean container |
| `make test-e2e` | 2 non-root devices with a realistic fake v1; real graphify; v1 fingerprinted |
| `make test-multi` (`ROUNDS=N`) | 5 device containers syncing concurrently; no lost writes |
| `make test-claude` | real Claude Code 2.1.285; layer 2 (real sessions) only with local `.env` token |
| `make test-migration` | `migrate/` scripts end to end: real v1 (origin/main) wired by its own sync.sh → export → new machine (failed precondition, mid-run failure + resume) → switch with carry-over (incl. both-changed) → rollback restores v1 exactly; `REAL_V1_EXPORT=…` for real data (local only) |
| `make test-scenarios` | migrate/ scripts 1 (~55), 2 (15), 3 (14 — real v1 via sync.sh) against messy states (failed stash pops, half-done git commands, leftover stashes, diverged remotes, odd files, reruns) judged by invariants: nothing lost, nothing broken committed, refusals leave v1 unchanged |
| `make test-published VERSION=… [INDEX=pypi\|testpypi]` | the PUBLISHED package, installed from the index as users do (no source in the image, no host folder mounted): 2 devices sync through a bare remote with the hooks as init-device wires them, signing, secret redaction, trust holds an unsigned push, files == the index's SHA-256 |
| `make test-teams` (`ROUNDS=N`) | 10 containers, 3 knowledge repos + an intruder, several project repos: planted secrets never reach a remote, untrusted pushes held back, index limit, init-device |

All of these except layer 2, `test-migration` and `test-scenarios` (they need the v1 code) run in CI.
`.env` (Claude token, testing ONLY) is gitignored + dockerignored; never print it.
Tests never touch the real user's Claude Code files (`real_user_files_untouched` in tests/conftest.py).

## Key constraints

- `.venv/bin/pytest` — never `source .venv/bin/activate`; no inline `python3 -c` (use `tmp.local/`)
- Code repo `scpg/corkern` (branch `main`). Feature branch → pull request → CI green → merge.
  The v1 code lives in a separate private repository (git remote `v1`) — never merge into it.
  Parity, migration and scenario tests need `git fetch v1` and run locally only (not in CI).
- Tests use an isolated git config (`isolated_git` fixture) — a global config may sign interactively
- CVE check before installing any new package (pip, uv, npm — also in Docker images)

## Project structure

```
corkern/
├── install.sh                 # installer (refuses while v1 is installed)
├── hooks/                     # bash hooks: corkern.sh entry, lib.sh, pull/push/commit-memory/notify
├── src/corkern/
│   ├── main.py                # Typer app, commands + v1 aliases, no-traceback wrapper
│   ├── guard.py               # v1 isolation guard — the only filesystem/process gateway
│   ├── config.py              # MemoryRepo, load_config, registry rules (v1 semantics)
│   ├── knowledge_repo.py      # template (.gitignore, .gitattributes, …) + init
│   ├── migrate.py             # v1 export bundle → knowledge repo
│   └── commands/              # one module per command group
├── tests/                     # pytest (parity, hooks, guard, status, …)
├── docker/                    # Dockerfile.{test,e2e,claude}, compose files, e2e/ multi/ claude/
└── .claude/skills/verify/     # project /verify recipe
```

### Knowledge repo layout
```
<knowledge-repo>/
├── corkern.conf            # slug:path registry (v1 projects.conf format)
├── corkern.conf.local      # gitignored per-machine overrides
├── .gitattributes          # union merge rules
├── memory/<slug>/          # memories, todo.md, sessions/, archive/
├── memory/global/          # cross-project memories
├── .devices/               # device manifests
├── .metrics.tsv            # session-start metrics (union-merged)
└── global/CLAUDE.md        # global Claude instructions
```
