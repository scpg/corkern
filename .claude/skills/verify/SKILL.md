---
name: verify
description: Drive corkern and install.sh end-to-end in an isolated sandbox (never the real HOME, never v1)
---

# Verifying corkern v2

Surfaces: the `corkern` CLI (`.venv/bin/corkern`, editable install = working tree) and `install.sh`.

## Sandbox (always)
Write an env file in the scratchpad and source it per command (shell state doesn't persist):

    S=<scratchpad>/verify; mkdir -p $S/home
    printf '[user]\n\tname = v\n\temail = v@v.invalid\n' > $S/gitconfig
    # env.sh:
    export HOME=$S/home GIT_CONFIG_GLOBAL=$S/gitconfig GIT_CONFIG_NOSYSTEM=1
    export C2=$PWD/.venv/bin/corkern                  # this checkout; set BEFORE overriding HOME
    export CORKERN_DEV=1                               # development checkout (a .venv copy otherwise refuses to act)
    export UV_TOOL_DIR=$S/uv/tools UV_TOOL_BIN_DIR=$S/uv/bin UV_CACHE_DIR=<real>/.cache/uv
    export CORKERN_CODE_REPO=$PWD CORKERN_CODE_REF=main   # this checkout as the code repo, no GitHub/ssh
    export PATH=$S/uv/bin:$PATH; unset CORKERN_MEMORY_DIR

Gotchas:
- A global git config may sign interactively (e.g. 1Password) → commits hang. Always isolate GIT_CONFIG_GLOBAL.
- A v1 check (e.g. a local `tmp.local/v1-check.sh`) uses `~` — run it with the REAL HOME (not after sourcing env.sh).
- Never run install.sh against the real HOME before UAT.
- Plain `git status` on v1 can rewrite v1's `.git/index` — use `GIT_OPTIONAL_LOCKS=0`
  (or `git --no-optional-locks`) for any read of v1.

## Extended (Docker)
`make test-e2e` — two non-root "devices" (alice, bob), each with a realistic fake v1
(real projects.conf format, ~/.claude symlinks, v1 `corkern` command, uncommitted work).
Runs install/refusals/migrate/day-to-day/second-device clone + full pytest, and
fingerprints v1 + ~/.claude before/after. Output: `══ E2E: ALL PHASES PASSED ══`.
To prove the suite can fail, sabotage `guard.assert_safe` inside the container only
(`docker compose ... run --rm corkern-e2e bash -c 'sed -i ... /src/src/corkern/guard.py; bash /src/docker/e2e/run.sh'`).

## Flows worth driving
- `init` (new / rerun / non-empty dir), `add` / `list` / `remove` / `status`
- v1 refusals: point CORKERN_MEMORY_DIR / init / migrate dest at real `~/dev/corkern`,
  `~/dev/corkern/memory/<slug>`, `~/.claude/projects/*/memory` → expect "v1-protected", exit 1
- `migrate` with the documented export: `git -C ~/dev/corkern archive HEAD memory .devices global projects.conf > $S/v1-export.tar`
  (read-only on v1); `--dry-run` then real; then `list` on the result
- Bundle probes: `tar -cPf` with absolute or `../` members → "unsafe entry"
- `install.sh --new`, rerun, `--clone <local repo>`, conflicting flags, v1 targets

## Before / after
Snapshot `stat` of ~/.local/bin/corkern{,2}; confirm ~/.local/share/corkern and ~/.corkern still
absent; check v1 is unchanged. Delete $S at the end — it may hold a copy of real memories.
