#!/usr/bin/env bash
# Step 1 of the v1 → v2 migration — run on a machine where corkern v1 is installed.
#
#   bash migrate/1-export-v1.sh [--out DIR] [--yes] [--dry-run] [--status]
#
# Commits v1's pending memory data (asks first; never code), records the export point, exports
# v1's committed data and packs everything the new machine needs into one folder (default
# ~/corkern-migration): the export, its checksum, a manifest and the scripts for the next steps.
# v1 keeps working; nothing in v1 is changed except that commit.
# shellcheck source=migrate/lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"
parse_common "$@"
OUT="$HOME/corkern-migration"
set -- "${ARGS[@]+"${ARGS[@]}"}"
while [ $# -gt 0 ]; do case "$1" in --out) OUT="$2"; shift ;; *) die "unknown option: $1" ;; esac; shift; done
STEPS=(commit-v1 integrate record-commit export verify pack)
if [ "$SHOW_STATUS" = 1 ]; then show_status "$SCRIPT_NAME" "${STEPS[@]}"; exit 0; fi
V1="$(v1_repo)"
DATA_PATHS=(memory .devices global projects.conf .metrics.tsv)

title "corkern migration — 1/3: export v1 ($(hostname))"
say "  log: $LOG"
title "Preconditions"
need "v1 repo at $V1"                       "set CORKERN=/path/to/v1 if it lives elsewhere" test -f "$V1/projects.conf"
need "git available"                        "install git" command -v git
say "  ${Y}!${N} Other v1 machines: push their memories first — only what reached v1's remote is exported"
[ -d "$V1/.git" ] && v1_state_needs "$V1"
v1_stray_needs "$V1"
EXPORTED=$(getvar v1_export_commit)
if [ -n "$EXPORTED" ]; then   # resuming: the export (or its first steps) must still match v1
    need "v1 unchanged since this export was started (${EXPORTED:0:12})" \
         "v1 has new memories since — run again with --restart for a fresh export" \
         bash -c "git -C '$V1' diff --quiet '$EXPORTED' -- memory global projects.conf projects.local.conf \
                  && [ -z \"\$(git -C '$V1' status --porcelain -- memory global projects.conf projects.local.conf)\" ]"
fi
need "output folder usable: $OUT"           "choose another one with --out DIR" \
     bash -c "mkdir -p '$OUT' && test -w '$OUT'"
warn_if_not "no Claude Code session running" "a running session can write memories during the export; close them" \
     bash -c "! pgrep -u $(id -u) -x claude"
warn_if_not "v1 remote reachable" "the commit is pushed later; v1 hooks retry" \
     timeout 10 git -C "$V1" ls-remote --quiet origin HEAD
end_preflight

commit_v1() {
    local pending conflicts
    conflicts=$(v1_conflicts "$V1")
    if [ -n "$conflicts" ]; then err "half-merged / conflict markers: $(echo "$conflicts" | tr '\n' ' ')— nothing committed"; return 1; fi
    pending=$(git -C "$V1" status --porcelain -- "${DATA_PATHS[@]}")
    if [ -z "$pending" ]; then ok "v1 data is fully committed"; else
        say "  Uncommitted v1 data (only data paths — code and other files are never touched):"
        printf '%s\n' "$pending" | sed 's/^/      /' | tee -a "$LOG"
        ask "Commit these in v1 so they are part of the export?" \
            || { err "not committed — they would be missing in v2"; return 1; }
        run git -C "$V1" add -A -- "${DATA_PATHS[@]}" || return 1
        run git -C "$V1" commit -q -m "memory: sync before v2 export" \
            || { say "      fix: if git signs your commits (e.g. 1Password), unlock it — then run again"; return 1; }
        ok "committed in v1"
    fi
    local other
    other=$(git -C "$V1" status --porcelain | grep -v -E ' (memory|\.devices|global|projects\.conf|\.metrics\.tsv)' || true)
    [ -n "$other" ] && warn "other uncommitted files in v1 are left alone: $(echo "$other" | awk '{print $2}' | tr '\n' ' ')"
    return 0
}

integrate() { v1_integrate; }

record_commit() {
    local c; c=$(git -C "$V1" rev-parse HEAD) || return 1
    setvar v1_export_commit "$c"
    ok "export point: $c"
}

export_v1() {
    if [ -f "$V1/export-import.py" ]; then
        run "$V1/corkern" export --out "$OUT/v1-export.tar" || return 1
        setvar format corkern-export
    else
        warn "this v1 has no 'corkern export' yet (update v1: git pull) — using git archive: committed data only"
        run git -C "$V1" archive -o "$OUT/v1-export.tar" HEAD memory .devices global projects.conf || return 1
        setvar format git-archive
    fi
    (cd "$OUT" && sha256sum v1-export.tar > v1-export.tar.sha256) || return 1
    ok "$OUT/v1-export.tar ($(du -h "$OUT/v1-export.tar" | cut -f1), $(getvar format))"
}

verify() {
    local in_tar in_v1 projects out
    if [ "$(getvar format)" = corkern-export ]; then
        out=$("$V1/corkern" import "$OUT/v1-export.tar" --check 2>&1); printf '%s\n' "$out" >> "$LOG"
        printf '%s\n' "$out" | grep -q "checksums match" || { err "the export does not verify"; return 1; }
        printf '%s\n' "$out" | grep -qE "missing here|different here" && { err "the export differs from v1 right after writing it"; return 1; }
        in_tar=$(tar -tzf "$OUT/v1-export.tar" | grep -c '^data/memory/')
        ok "verified: $in_tar memory files, every checksum matches v1"
        setvar memory_files "$in_tar"
        return 0
    fi
    in_tar=$(tar -tf "$OUT/v1-export.tar" | grep -c '^memory/.*[^/]$')
    in_v1=$(git -C "$V1" ls-files memory | wc -l)
    projects=$(grep -cvE '^\s*(#|$)' "$V1/projects.conf")
    [ "$in_tar" = "$in_v1" ] || { err "export has $in_tar memory files, v1 has $in_v1"; return 1; }
    ok "$in_tar memory files, $projects project line(s) — matches v1's committed data"
    setvar memory_files "$in_tar"
}

pack() {
    cp "$HERE"/lib.sh "$HERE"/2-setup-new-machine.sh "$HERE"/3-switch-v1-machine.sh \
       "$HERE"/rollback-v1-machine.sh "$HERE"/status.sh "$HERE"/README.md "$OUT"/ || return 1
    {
        echo "source_host=$(hostname)"
        echo "v1_export_commit=$(getvar v1_export_commit)"
        echo "created=$(_ts)"
        echo "memory_files=$(getvar memory_files)"
        echo "sha256=$(cut -d' ' -f1 "$OUT/v1-export.tar.sha256")"
        echo "format=$(getvar format)"
    } > "$OUT/manifest.txt"
    chmod -R go-rwx "$OUT"
    ok "everything for the new machine is in $OUT"
}

title "Steps"
step commit-v1     "Commit v1's pending memory data"          commit_v1
step integrate     "Bring in what other v1 machines pushed"   integrate
step record-commit "Record the export point"                  record_commit
step export        "Export v1's committed data"               export_v1
step verify        "Check the export against v1"              verify
step pack          "Pack export + scripts for the new machine" pack

title "Done"
say "  Copy the folder to the new machine (it holds your private memories — not via public services):"
say "      scp -r $OUT <new-machine>:"
say "  Then, on the new machine:"
say "      bash ~/$(basename "$OUT")/2-setup-new-machine.sh --repo <you>/<private-repo-name>"
say "  Log: $LOG"
