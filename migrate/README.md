# Migrating from corkern v1 to v2

Three scripts, run in this order. Each one checks all its preconditions first and changes
nothing if one is not met (it says how to fix it). Steps are recorded as they finish: after a
problem, fix it and run the same script again — it continues where it stopped. Everything is
logged to `~/.local/state/corkern-migration/<script>.log`.

| Where | Script | What it does |
|---|---|---|
| a v1 machine (once) | `bash migrate/1-export-v1.sh` | commits v1's pending memory data (asks), exports it, packs `~/corkern-migration/` with these scripts |
| the new machine | `bash ~/corkern-migration/2-setup-new-machine.sh --repo <you>/<name>` | installs v2 from the export, checks for secrets, publishes a private repo, keys, trust, wires Claude Code, IWE, tests a sync |
| each v1 machine, after v2 proved itself | `bash migrate/3-switch-v1-machine.sh --repo <you>/<name>` | last v1 sync, backup, retires v1 (kept as `~/dev/corkern-v1-retired`), installs v2, carries over what v1 wrote meanwhile (and this machine's `projects.local.conf` paths → `corkern.conf.local`), keys, trust, wires Claude Code |
| if needed | `bash migrate/rollback-v1-machine.sh` | puts v1 back exactly as it was |

Scripts 1 and 3 refuse to start while v1's git state could lose data or commit something
broken. That covers:
- half-merged files or conflict markers;
- a merge, rebase, cherry-pick, revert or bisect that was not finished;
- a detached HEAD, another branch, or `main` without its upstream;
- a leftover `index.lock`;
- no git identity;
- memory lines that exist only in a `git stash`.

A stash whose lines are all in the files already (a failed autostash pop that you resolved) only
gets a warning. When another v1 machine pushed meanwhile, conflicting `MEMORY.md` indexes,
`projects.conf` and `.metrics.tsv` keep the lines of both sides. The exceptions are the same
project with two different paths, and any other file that conflicts. In those cases v1 is put
back as it was and the script says what to do. Script 1 also refuses to resume when v1 has
changed since its export was started, so an old export is never handed on silently. Every
case is tested in `make test-scenarios`. Script 2 refuses, before installing anything, an export
that is damaged, incomplete, from a newer corkern or holds conflict markers, a remote that
already has commits, and a machine where git does not know your name/email yet.

Script 3 does not trust the knowledge repo it clones. Before v1 is touched it checks the export
point named in `.corkern-migration`: it must be a commit id that this v1 has (otherwise: fetch v1
first) and an ancestor of v1's history, and where this machine ran script 1 it must equal script 1's
own record (both values are shown when they differ). The carry-over never writes through a symlink
or outside the repo (such files are skipped and listed in the report; v1's versions stay in the
retired repo), keeps a last line without a newline, says "combined" only when every line arrived,
and commits only after the hooks' secret scan. A separate scan step checks everything that will be
pushed and stops before the push if a commit holds a secret.

To redo a script from the start: `--restart` (the previous progress is kept as `*.state.*.old`).
Any time: `bash migrate/status.sh` (where this machine stands), `--status` on a script (its
steps), `--dry-run` (only the precondition check).

Close Claude Code sessions before steps 1 and 3. The export folder holds your private memories —
copy it with `scp` or a USB stick, not through public services.
