# shellcheck shell=bash
# shellcheck disable=SC2034  # YES/DRY/SHOW_STATUS/HERE are used by the scripts that source this file
# Shared by the migration scripts: preconditions, resumable steps, state, log.
#
#   Every script first checks ALL its preconditions (need / warn_if) and stops before changing
#   anything if one fails, printing how to fix it. Then it runs its steps in order. A finished
#   step is recorded in the state file and skipped on the next run, so after a failure you fix
#   the cause and simply run the script again — it continues where it stopped.
#   Everything (commands, output, answers) is appended to the log file.
#
#   Common options: --status (show progress, change nothing), --dry-run (only check the
#   preconditions), --yes (answer yes to every question — for tests and repeat runs).
set -uo pipefail

MIG_DIR="${CORKERN_MIGRATION_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/corkern-migration}"
mkdir -p "$MIG_DIR" && chmod 700 "$MIG_DIR"
SCRIPT_NAME="$(basename "${0%.sh}")"
LOG="$MIG_DIR/$SCRIPT_NAME.log"
STATE="$MIG_DIR/$SCRIPT_NAME.state"
touch "$STATE"
# shellcheck disable=SC2034  # used by the scripts that source this file
YES=0 DRY=0 SHOW_STATUS=0
FAILED_NEEDS=0
# shellcheck disable=SC2034
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

if [ -t 1 ]; then B=$'\e[1m' G=$'\e[32m' Y=$'\e[33m' R=$'\e[31m' C=$'\e[36m' N=$'\e[0m'
else B="" G="" Y="" R="" C="" N=""; fi

_ts() { date '+%Y-%m-%d %H:%M:%S'; }
logf() { printf '%s %s\n' "$(_ts)" "$*" >> "$LOG"; }
say()  { printf '%s\n' "$*"; logf "$*"; }
ok()   { say "  ${G}✓${N} $*"; }
warn() { say "  ${Y}!${N} $*"; }
err()  { say "  ${R}✗${N} $*"; }
title(){ say ""; say "${B}${C}$*${N}"; }
die()  { err "$*"; say ""; say "  Log: $LOG"; exit 1; }

# parse_common "$@" — handles --status / --dry-run / --yes; leaves the rest in ARGS
parse_common() {
    ARGS=()
    for a in "$@"; do
        case "$a" in
            --yes|-y)  YES=1 ;;
            --dry-run) DRY=1 ;;
            --status)  SHOW_STATUS=1 ;;
            --restart) RESTART=1 ;;
            *)         ARGS+=("$a") ;;
        esac
    done
    if [ "${RESTART:-0}" = 1 ] && { [ "$DRY" = 1 ] || [ "$SHOW_STATUS" = 1 ]; }; then
        STATE=$(mktemp)   # check as a restart would, but keep the real progress untouched
    elif [ "${RESTART:-0}" = 1 ] && [ -s "$STATE" ]; then
        mv "$STATE" "$STATE.$(date +%Y%m%d-%H%M%S).old" && touch "$STATE"
        logf "restart: previous progress kept as $STATE.*.old"
    fi
    logf "──── run: $SCRIPT_NAME $* (user $USER, host $(hostname))"
}

# ── state ───────────────────────────────────────────────────────────────────────────────
done_step()  { grep -q "^step:$1=" "$STATE"; }
mark_done()  { printf 'step:%s=%s\n' "$1" "$(_ts)" >> "$STATE"; }
setvar()     { local k="$1" v="$2"; grep -v "^var:$k=" "$STATE" > "$STATE.tmp" || true; printf 'var:%s=%s\n' "$k" "$v" >> "$STATE.tmp"; mv "$STATE.tmp" "$STATE"; }
getvar()     { grep "^var:$1=" "$STATE" | tail -1 | cut -d= -f2-; }

# ── preconditions ───────────────────────────────────────────────────────────────────────
# need "description" "how to fix" command... — must succeed, else the script stops before step 1
need() {
    local desc="$1" fix="$2"; shift 2
    if "$@" >> "$LOG" 2>&1; then ok "$desc"
    else err "$desc"; say "      fix: $fix"; FAILED_NEEDS=$((FAILED_NEEDS + 1)); fi
}
# warn_if_not "description" "consequence / hint" command... — reported, not fatal
warn_if_not() {
    local desc="$1" hint="$2"; shift 2
    if "$@" >> "$LOG" 2>&1; then ok "$desc"; else warn "$desc — $hint"; fi
}
end_preflight() {
    if [ "$FAILED_NEEDS" -gt 0 ]; then
        die "$FAILED_NEEDS precondition(s) not met — nothing was changed. Fix them and run again."
    fi
    ok "all preconditions met"
    if [ "$DRY" = 1 ]; then say ""; say "  --dry-run: stopping here, nothing was changed."; exit 0; fi
}

# ── questions ───────────────────────────────────────────────────────────────────────────
ask() {   # ask "question" → 0 for yes (default no)
    if [ "$YES" = 1 ]; then logf "ask: $1 → yes (--yes)"; return 0; fi
    local answer=""
    printf '  %s [y/N] ' "$1"
    read -r answer < /dev/tty || answer=""
    logf "ask: $1 → ${answer:-no answer}"
    [[ "$answer" =~ ^[yY] ]]
}

# ── steps ───────────────────────────────────────────────────────────────────────────────
# step NAME "what it does" function — runs once; a failure stops the script (re-run resumes)
STEP_NO=0
step() {
    local name="$1" desc="$2" fn="$3"
    STEP_NO=$((STEP_NO + 1))
    if done_step "$name"; then
        say "  ${G}✓${N} ${STEP_NO}. $desc ${C}(done $(grep "^step:$name=" "$STATE" | tail -1 | cut -d= -f2))${N}"
        return 0
    fi
    title "${STEP_NO}. $desc"
    logf "step start: $name"
    if "$fn"; then
        mark_done "$name"; logf "step done: $name"
    else
        die "step ${STEP_NO} ($name) failed — fix the cause above and run this script again; it resumes here.${STEP_FAIL_HINT:+ $STEP_FAIL_HINT}"
    fi
}

# run cmd... — show + log the command and its output; returns the command's exit code
run() {
    logf "\$ $*"
    "$@" 2>&1 | tee -a "$LOG"
    return "${PIPESTATUS[0]}"
}

# status_of SCRIPT step-name... — print progress (for --status and status.sh)
show_status() {
    local state="$MIG_DIR/$1.state"; shift
    printf '%s\n' "${B}$1${N}"
    [ -s "$state" ] || { echo "  not started"; return; }
    for s in "$@"; do
        if grep -q "^step:$s=" "$state"; then echo "  ${G}✓${N} $s  ($(grep "^step:$s=" "$state" | tail -1 | cut -d= -f2))"
        else echo "  · $s"; fi
    done
}

# ── shared facts ────────────────────────────────────────────────────────────────────────
v1_repo()        { echo "${CORKERN:-$HOME/dev/corkern}"; }
# files git left half-merged (e.g. a failed autostash pop — no MERGE_HEAD then) or that contain
# conflict markers: such files must never be committed or exported
v1_conflicts()   {
    local repo="$1"
    { git -C "$repo" diff --name-only --diff-filter=U
      (cd "$repo" && grep -rlIsE '^(<<<<<<< |>>>>>>> )' -- memory .devices global projects.conf projects.local.conf .metrics.tsv)
    } | sort -u
}
# a git command that was started and not finished (merge, rebase, cherry-pick, revert, bisect)
v1_in_progress() {
    local repo="$1" p
    for p in MERGE_HEAD:merge rebase-merge:rebase rebase-apply:rebase CHERRY_PICK_HEAD:cherry-pick \
             REVERT_HEAD:revert BISECT_LOG:bisect; do
        [ -e "$(git -C "$repo" rev-parse --path-format=absolute --git-path "${p%%:*}")" ] && echo "${p#*:}"
    done | sort -u
}
# what is wrong with v1's branch for an export: detached, not main, or main without its upstream
v1_branch_problem() {
    local repo="$1" b
    b=$(git -C "$repo" symbolic-ref -q --short HEAD) || { echo "detached HEAD (no branch checked out)"; return; }
    [ "$b" = main ] || { echo "on branch '$b', not main"; return; }
    git -C "$repo" remote get-url origin >/dev/null 2>&1 || return 0
    git -C "$repo" rev-parse -q --verify '@{upstream}' >/dev/null 2>&1 || echo "main has no upstream (origin/main)"
}
# lines that only exist in a stash: "stash@{N} <file>: <line>" for every line a stash adds to a
# memory/registry file that is not in that file now. A failed autostash pop that was resolved by
# hand leaves the stash behind although its lines are in the files → nothing is printed then.
# NOISE=1: the same for v1's own statistics (.metrics.tsv, .devices/) instead.
V1_STASH_DATA=(memory global projects.conf projects.local.conf)
V1_STASH_NOISE=(.metrics.tsv .devices)
v1_stash_only() {
    local repo="$1" n i s f line
    local -a paths=("${V1_STASH_DATA[@]}"); [ "${NOISE:-0}" = 1 ] && paths=("${V1_STASH_NOISE[@]}")
    n=$(git -C "$repo" stash list | wc -l)
    for ((i = 0; i < n; i++)); do
        s="stash@{$i}"
        while IFS= read -r f; do
            [ -n "$f" ] || continue
            git -C "$repo" diff "$s^1" "$s" -- "$f" | sed -n 's/^+\([^+].*\)$/\1/p; s/^+$//p' \
              | while IFS= read -r line; do
                  [ -z "$line" ] || grep -qxF -- "$line" "$repo/$f" 2>/dev/null || echo "$s $f: $line"
                done
        done < <(git -C "$repo" diff --name-only "$s^1" "$s" -- "${paths[@]}")
        git -C "$repo" rev-parse -q --verify "$s^3" >/dev/null || continue   # untracked files (stash -u)
        while IFS= read -r f; do
            git -C "$repo" show "$s^3:$f" | while IFS= read -r line; do
                [ -z "$line" ] || grep -qxF -- "$line" "$repo/$f" 2>/dev/null || echo "$s $f: $line"
            done
        done < <(git -C "$repo" ls-tree -r --name-only "$s^3" -- "${paths[@]}")
    done
}
# bash -c "$(v1_fns) …" — the helpers above, for need/warn_if_not checks
v1_fns() { declare -p V1_STASH_DATA V1_STASH_NOISE; declare -f v1_conflicts v1_in_progress v1_branch_problem v1_stash_only; }
# v1 installs `corkern` as a link into its repo; v2's `corkern` is uv's/pipx's command
v1_command()     { [ -L "$HOME/.local/bin/corkern" ] && [ -e "$(dirname "$(realpath -- "$HOME/.local/bin/corkern")")/projects.conf" ]; }
v2_cli()         { [ -x "$HOME/.local/bin/corkern" ] && ! v1_command; }
v1_installed()   { [ -f "$(v1_repo)/projects.conf" ] || v1_command || [ -e "$HOME/.claude/corkern.sh" ]; }
claude_running() { pgrep -u "$(id -u)" -x claude >/dev/null 2>&1; }
py_ok()          { python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; }
kr()             { echo "${CORKERN_MEMORY_DIR:-$HOME/.corkern}"; }
c2()             { CORKERN_MEMORY_DIR="$(kr)" "$HOME/.local/bin/corkern" "$@"; }
hook()           { echo '{}' | CORKERN_MEMORY_DIR="$(kr)" CORKERN_HOOK_FOREGROUND=1 bash "$HOME/.local/share/corkern/hooks/corkern.sh" "$1"; }

# v1_state_needs V1 — preconditions shared by scripts 1 and 3: v1's git state must be one that
# can be exported/committed without losing data or committing something broken
v1_state_needs() {
    local v1="$1" x
    x=$(v1_in_progress "$v1" | tr '\n' ' ')
    need "no git command half-done in v1 (merge, rebase, cherry-pick, revert, bisect)" \
         "a ${x% } was started in $v1 and not finished — finish or abort it (git -C '$v1' status says how), then run again" test -z "$x"
    x=$(v1_conflicts "$v1" | tr '\n' ' ')
    need "no half-merged files or conflict markers in v1" \
         "resolve ${x:-each file }(keep both sides, remove the <<<<<<< ======= >>>>>>> lines; a memory that only documents such lines: indent them), then: git -C '$v1' add <file>; if 'git stash list' shows an autostash, keep it until you checked it" \
         test -z "$x"
    x=$(v1_branch_problem "$v1")
    need "v1 on branch main, following origin/main" \
         "${x:-} — git -C '$v1' checkout main, or: git -C '$v1' branch -u origin/main" test -z "$x"
    need "no other git command running in v1 (index.lock)" \
         "a git command or v1 hook is still running — wait; if none is (ps aux | grep git), remove $(git -C "$v1" rev-parse --path-format=absolute --git-path index.lock)" \
         test ! -e "$(git -C "$v1" rev-parse --path-format=absolute --git-path index.lock)"
    need "git knows who commits (user.name / user.email)" \
         "git config --global user.name 'Your Name'; git config --global user.email you@example.com" \
         bash -c "git -C '$v1' config user.name && git -C '$v1' config user.email"
    x=$(v1_stash_only "$v1")
    need "no memories that exist only in a git stash" \
         "look: git -C '$v1' stash show -p stash@{N}; keep them: git -C '$v1' stash pop (keep both sides); not needed: git -C '$v1' stash drop" \
         test -z "$x"
    [ -n "$x" ] && printf '%s\n' "$x" | head -8 | sed 's/^/      only in a stash → /' | tee -a "$LOG"
    local n; n=$(git -C "$v1" stash list | wc -l)
    if [ -z "$x" ] && [ "$n" -gt 0 ]; then
        warn "v1 has $n stash entr$([ "$n" = 1 ] && echo y || echo ies) (git -C '$v1' stash list) — every memory line in them is also in the files$([ -n "$(NOISE=1 v1_stash_only "$v1")" ] && echo '; only metrics/device stamps differ (not needed)'). Kept as they are."
    fi
}

# ── bringing in what other v1 machines pushed (scripts 1 and 3; use the global V1) ──────────────
# .metrics.tsv is statistics: on a conflict keep every row from both sides (header first)
_union_metrics() {
    { git -C "$V1" show :2:.metrics.tsv | head -1
      { git -C "$V1" show :2:.metrics.tsv; git -C "$V1" show :3:.metrics.tsv; } | grep -v '^date' | awk '!seen[$0]++' | sort
    } > "$V1/.metrics.tsv.union" && mv "$V1/.metrics.tsv.union" "$V1/.metrics.tsv" && git -C "$V1" add .metrics.tsv
}

# MEMORY.md indexes and the registry are lists: on a conflict keep the lines of both sides
_union_file() {
    local f="$1" t; t=$(mktemp -d)
    git -C "$V1" show ":1:$f" > "$t/base" 2>/dev/null || : > "$t/base"
    { git -C "$V1" show ":2:$f" > "$t/ours" && git -C "$V1" show ":3:$f" > "$t/theirs"; } 2>/dev/null || { rm -rf "$t"; return 1; }
    git merge-file --union -p "$t/ours" "$t/base" "$t/theirs" > "$V1/$f"; rm -rf "$t"
    ! grep -qE '^(<<<<<<< |>>>>>>> )' "$V1/$f" && git -C "$V1" add -- "$f"
}
_dup_slugs() { grep -vE '^[[:space:]]*(#|$)' "$V1/projects.conf" 2>/dev/null | cut -d: -f1 | sort | uniq -d; }

v1_integrate() {
    git -C "$V1" remote get-url origin >/dev/null 2>&1 || { ok "v1 has no remote — nothing to bring in"; return 0; }
    if ! run git -C "$V1" fetch -q origin; then
        warn "v1's remote is not reachable — what other machines pushed would be missing"
        ask "Continue without it?" || return 1
        return 0
    fi
    if git -C "$V1" merge-base --is-ancestor '@{upstream}' HEAD 2>/dev/null; then ok "nothing new from other machines"
    else
        local incoming; incoming=$(git -C "$V1" rev-list --count 'HEAD..@{upstream}')
        say "  Other machines pushed to v1:"
        git -C "$V1" log --format='      %h %ad %an  %s' --date=short 'HEAD..@{upstream}' | tee -a "$LOG"
        if ! run git -C "$V1" rebase -q '@{upstream}'; then
            local conflicts f bad tries=0
            while [ -d "$V1/.git/rebase-merge" ] || [ -d "$V1/.git/rebase-apply" ]; do
                conflicts=$(git -C "$V1" diff --name-only --diff-filter=U) bad=""
                while IFS= read -r f; do
                    case "$f" in
                        "") ;;
                        .metrics.tsv) _union_metrics || bad+="$f " ;;
                        projects.conf|memory/MEMORY.md|memory/*/MEMORY.md) _union_file "$f" || bad+="$f " ;;
                        *) bad+="$f " ;;
                    esac
                done <<< "$conflicts"
                [[ $'\n'"$conflicts"$'\n' == *$'\nprojects.conf\n'* ]] && [ -n "$(_dup_slugs)" ] && bad+="projects.conf (both machines gave '$(_dup_slugs | tr '\n' ' ')' a different path) "
                tries=$((tries + 1)); [ "$tries" -gt 100 ] && bad+="(the rebase does not finish) "
                if [ -n "$bad" ]; then
                    git -C "$V1" rebase --abort
                    err "conflict in: $bad— v1 is back exactly as before"
                    say "      fix: in $V1 run 'git pull --rebase', keep both versions of each memory file, then run this script again"
                    return 1
                fi
                GIT_EDITOR=true run git -C "$V1" rebase --continue
            done
        fi
        ok "brought in $incoming commit(s) from other machines"
    fi
    run git -C "$V1" push -q || warn "push failed — v1's remote gets it with the next v1 sync"
    return 0
}

# v1_stray_needs V1 — memories in ~/dev/corkern while v1 lives elsewhere (host-b: v1 on /mnt/c) are
# never synced by v1 and would be left behind. Claude can create that folder itself, following
# instructions that name ~/dev/corkern/memory/<slug>/sessions/.
v1_stray_needs() {
    local v1="$1" stray="$HOME/dev/corkern"
    [ -d "$stray/memory" ] || return 0
    [ "$(readlink -f "$stray")" = "$(readlink -f "$v1")" ] && return 0
    need "no stray memory folder at $stray (v1 is $v1)" \
         "memories there were never synced — compare: diff -rq '$stray/memory' '$v1/memory' | grep -v '^Only in $v1'; copy what is missing (cp -n), then rename it: mv '$stray' '$stray-stray'" \
         false
}
