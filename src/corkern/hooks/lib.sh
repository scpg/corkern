# shellcheck shell=bash
# Shared helpers for corkern v2 hooks.
#
# Contract: hooks never block or break Claude Code. Every failure path exits 0 and
# leaves a diagnostic — sync problems in $KR/.last-sync-<hook>-error (v1 format),
# setup problems in $CORKERN_STATE/last-hook-problem — for `corkern status`.
# Hooks print nothing on stdout (SessionStart stdout would be injected into Claude's context).

CORKERN_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/corkern"
CK_FORMAT=1   # knowledge repo format these hooks understand (src/corkern/knowledge_repo.py FORMAT_VERSION)
export GIT_TERMINAL_PROMPT=0   # never wait for a credential prompt

ck_note() {
    mkdir -p "$CORKERN_STATE" 2>/dev/null || return 0
    printf '%s %s: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2" > "$CORKERN_STATE/last-hook-problem"
}

# ── v1 guard — same rules as src/corkern/guard.py (tests/test_hooks.py checks parity) ──
ck_real_home() { getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6; }

ck_is_v1() {
    local given="$1" form root r c rh
    local -a homes=("$HOME") roots=()
    rh=$(ck_real_home) && [ -n "$rh" ] && homes+=("$rh")
    for h in "${homes[@]}"; do roots+=("$h/dev/corkern" "$h/.claude")
        [ -L "$h/.local/bin/corkern" ] && [ -e "$(dirname "$(realpath -- "$h/.local/bin/corkern")")/projects.conf" ] \
            && roots+=("$h/.local/bin/corkern"); done
    [ -n "${CORKERN:-}" ] && roots+=("$CORKERN")
    for form in "$(realpath -m -s -- "$given")" "$(realpath -m -- "$given")"; do
        for root in "${roots[@]}"; do
            for r in "$(realpath -m -s -- "$root")" "$(realpath -m -- "$root")"; do
                if [ "$form" = "$r" ] || [[ "$form" == "$r"/* ]]; then return 0; fi
            done
        done
        c="$form"
        while :; do
            [ -e "$c/.corkern-v1.sentinel" ] && return 0
            [ -e "$c/projects.conf" ] && return 0
            [ "$c" = "/" ] && break
            c=$(dirname "$c")
        done
    done
    return 1
}

# ck_machine_repo — this machine's knowledge repo, as recorded by `corkern setup` in
# machine.conf; older installs recorded it in the corkern wrapper written by install.sh
ck_machine_repo() {
    local conf="${XDG_CONFIG_HOME:-$HOME/.config}/corkern/machine.conf" w v=""
    [ -f "$conf" ] && v=$(sed -n 's/^[[:space:]]*memory_dir[[:space:]]*=[[:space:]]*\(.*[^[:space:]]\)[[:space:]]*$/\1/p' "$conf" 2>/dev/null | head -1)
    if [ -z "$v" ]; then
        w=$(command -v corkern2 2>/dev/null) || w="$HOME/.local/bin/corkern2"   # install.sh wrapper (before 2.0)
        [ -f "$w" ] && v=$(sed -n "s/^CORKERN_MACHINE_REPO='\(.*\)'\$/\1/p" "$w" 2>/dev/null | head -1)
    fi
    printf '%s' "$v"
}

# ck_init HOOK — resolve the knowledge repo into $KR; returns 1 if the hook must be a no-op
ck_init() {
    KR="${CORKERN_MEMORY_DIR:-}"
    # not in the environment (settings.json stays machine-neutral for shared dotfiles): this
    # machine's knowledge repo is the one install.sh wrote into this machine's corkern wrapper
    [ -n "$KR" ] || KR=$(ck_machine_repo)
    if [ -z "$KR" ]; then ck_note "$1" "CORKERN_MEMORY_DIR is not set (and no corkern wrapper — rerun install.sh)"; return 1; fi
    export CORKERN_MEMORY_DIR="$KR"
    KR=$(realpath -m -- "$KR")
    if ck_is_v1 "$KR"; then ck_note "$1" "refused: $KR is v1-protected"; return 1; fi
    if [ ! -f "$KR/corkern.conf" ] || [ ! -d "$KR/.git" ]; then
        ck_note "$1" "$KR is not a corkern knowledge repo"; return 1
    fi
    local fmt
    fmt=$(sed -n 's/^knowledge_repo_format=\([0-9]*\)$/\1/p' "$KR/.corkern-format" 2>/dev/null)
    if [ -n "$fmt" ] && [ "$fmt" -gt "$CK_FORMAT" ]; then
        ck_note "$1" "knowledge repo format $fmt is newer than these hooks understand ($CK_FORMAT) — update corkern (install.sh)"
        return 1
    fi
    return 0
}

ck_git() { git -C "$KR" "$@"; }

# ck_lock SECONDS — serialize git work across hooks and sessions (fd 9 held until exit)
ck_lock() { exec 9>"$KR/.corkern.lock" && flock -w "${1:-10}" 9; }

ck_stamp() { date '+%Y-%m-%d %H:%M:%S' > "$KR/.last-sync-$1"; rm -f "$KR/.last-sync-$1-error"; }

# ck_due WHAT SECONDS — true if the last WHAT (pull/push) is older than SECONDS (or never)
ck_due() {
    local f="$KR/.last-sync-$1" ts
    [ -f "$f" ] || return 0
    ts=$(date -d "$(cat "$f")" +%s 2>/dev/null) || return 0
    [ $(( $(date +%s) - ts )) -ge "$2" ]
}

ck_error() {
    local hook="$1" reason="$2" detail="${3:-}"
    {
        echo "timestamp : $(date '+%Y-%m-%d %H:%M:%S')"
        echo "action    : $hook"
        echo "machine   : $(hostname -s 2>/dev/null || echo unknown)"
        echo "remote    : $(ck_git remote get-url origin 2>/dev/null || echo none)"
        echo "branch    : $(ck_git branch --show-current 2>/dev/null || echo unknown)"
        echo "reason    : $reason"
        [ -n "$detail" ] && echo "detail    : $detail"
    } > "$KR/.last-sync-$hook-error"
}

# ck_preflight HOOK — can we talk to the remote right now?
ck_preflight() {
    if ! ck_git remote get-url origin >/dev/null 2>&1; then
        ck_error "$1" "no remote configured" "run: git -C $KR remote add origin <url>"; return 1
    fi
    if [ -f "$KR/.git/MERGE_HEAD" ] || [ -d "$KR/.git/rebase-merge" ] || [ -d "$KR/.git/rebase-apply" ]; then
        ck_error "$1" "repo is in a merge/rebase state" "resolve it in $KR"; return 1
    fi
    if ! timeout 10 git -C "$KR" ls-remote --quiet origin HEAD >/dev/null 2>&1; then
        ck_error "$1" "remote not reachable" "check network / SSH key"; return 1
    fi
    return 0
}

ck_memory_dirty() {
    ! ck_git diff --quiet -- memory/ 2>/dev/null \
        || ! ck_git diff --cached --quiet -- memory/ 2>/dev/null \
        || [ -n "$(ck_git ls-files --others --exclude-standard -- memory/ 2>/dev/null)" ]
}

# ── Secrets never leave the machine ────────────────────────────────────────────────────
# Same pre-filter as src/corkern/secret_scan.py HINT (tests check they match): a staged file
# without any of these words cannot hold a secret, so Python is only started when needed.
CK_SECRET_HINT='PRIVATE KEY|gh[pousr]_|github_pat_|sk-|AKIA|ASIA|xox[abprs]-|AIza|glpat-|npm_|passw|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key'

ck_bin() {  # path of the corkern v2 CLI (never v1's `corkern` link into its repo), or nothing
    local b
    for b in "$(command -v corkern 2>/dev/null)" "$HOME/.local/bin/corkern"; do
        [ -n "$b" ] && [ -x "$b" ] || continue
        [ -L "$b" ] && [ -e "$(dirname "$(realpath -- "$b")")/projects.conf" ] && continue
        echo "$b"; return 0
    done
    return 1
}

# ck_scan_staged — before every commit: secrets in staged files are replaced by a marker
# (`corkern secrets --redact`; the original stays on this machine) and the redacted file is
# staged instead. If corkern cannot check a suspicious file, NOTHING is committed (returns 1).
ck_scan_staged() {
    local f bin out
    local -a staged=() suspect=()
    while IFS= read -r -d '' f; do staged+=("$f"); done \
        < <(ck_git diff --cached --name-only --no-renames -z --diff-filter=AM 2>/dev/null)
    [ "${#staged[@]}" -gt 0 ] || return 0
    while IFS= read -r -d '' f; do suspect+=("$f"); done \
        < <(cd "$KR" && grep -lZiE -- "$CK_SECRET_HINT" "${staged[@]}" 2>/dev/null)
    [ "${#suspect[@]}" -gt 0 ] || return 0
    bin=$(ck_bin)
    if [ -z "$bin" ] || ! out=$(cd "$KR" && "$bin" secrets --redact "${suspect[@]}" 2>/dev/null); then
        ck_git reset -q >/dev/null 2>&1
        ck_note commit "not committed: could not check ${suspect[*]} for secrets (corkern missing or failed) — run: corkern secrets --scan"
        return 1
    fi
    ck_git add -- "${suspect[@]}" 2>/dev/null
    while IFS= read -r f; do            # binary / huge files that mention a secret word: keep local
        [ -n "$f" ] || continue
        ck_git reset -q -- "$f" >/dev/null 2>&1
        ck_note commit "not committed: $f could not be checked for secrets (binary or over 2 MB) — remove it from memory/ or check it by hand"
    done < <(printf '%s\n' "$out" | sed -n 's/^HOLD //p')
    return 0
}

# ck_commit_memory MESSAGE-PREFIX — stage memory/ (incl. new files) and commit with file names
ck_commit_memory() {
    ck_git add -A -- memory/ 2>/dev/null || return 1
    ck_scan_staged || return 1
    local changed
    changed=$(ck_git -c core.quotePath=false diff --cached --name-only -- memory/ 2>/dev/null \
        | sed 's|^memory/||' | tr '\n' ' ')
    changed="${changed% }"
    [ -n "$changed" ] || return 0
    ck_git commit -q -m "$1: $changed" 2>/dev/null
    # shellcheck disable=SC2034  # read by the sourcing hook script
    COMMITTED="$changed"
}

# ck_pull HOOK — integrate the remote. Never leaves the repo stuck, never loses text:
#   1. local memory changes (incl. new files) are committed first, so an untracked file
#      at an incoming path cannot block the pull
#   2. rebase (linear history); MEMORY.md indexes merge line-wise via .gitattributes union
#   3. if the rebase conflicts: merge instead, keeping BOTH versions of each conflicting
#      file — remote at the path, local next to it as <name>.conflict-<host>-<time><.ext>
#
# No --autostash: when a write lands mid-pull, autostash re-applies it afterwards and, if that
# file also changed upstream, leaves it ONLY in the stash — never committed (found by the
# 5-device stress test). Instead: fetch (network, no file changes), commit everything, then
# rebase locally (milliseconds). If a write sneaks in, git refuses → commit it and retry.
ck_pull() {
    local out tries
    out=$(ck_git fetch -q 2>&1) || { ck_error "$1" "git fetch failed" "$out"; return 1; }
    ck_track_upstream || { ck_commit_all "auto: sync" || true; ck_stamp pull; return 0; }  # empty remote
    ck_verify_incoming "$1" || { ck_commit_all "auto: sync" || true; return 1; }
    for tries in 1 2 3; do
        ck_commit_all "auto: sync" || true
        if out=$(ck_git rebase -q '@{upstream}' 2>&1); then ck_stamp pull; return 0; fi
        if ck_in_progress; then                      # real conflict → keep both versions
            ck_git rebase --abort >/dev/null 2>&1
            ck_merge_keep_both "$1" "$out"
            return
        fi
        # Refused without starting: a write landed after our commit → commit it and retry.
        # Anything else is a real error — report git's own message.
        if [ -z "$(ck_git status --porcelain 2>/dev/null)" ]; then
            ck_error "$1" "git rebase failed" "$out"; return 1
        fi
        [ "$tries" -lt 3 ] && sleep 0.2
    done
    ck_error "$1" "pull kept racing with local writes; will retry next time" "$out"
    return 1
}

# ── Only integrate what a trusted device signed ───────────────────────────────────────────
# Verification is on when this machine has a trust store (`corkern trust --enable`): the keys
# THIS machine confirmed. It lives in .git/, so it is never synced — a commit that adds a key to
# the synced .devices/allowed_signers cannot vouch for itself. Incoming commits that are
# unsigned or signed by an untrusted key are not integrated; they are listed in
# .last-sync-untrusted until `corkern trust` accepts them (or their device).
ck_verify_incoming() {
    local trust="$KR/.git/corkern/trusted_signers" accepted="$KR/.git/corkern/accepted" sha
    local -a held=()
    if [ ! -f "$trust" ]; then rm -f "$KR/.last-sync-untrusted"; return 0; fi
    while IFS= read -r sha; do
        [ -f "$accepted" ] && grep -qx -- "$sha" "$accepted" && continue
        ck_git -c gpg.ssh.allowedSignersFile="$trust" -c gpg.ssh.program=ssh-keygen \
            verify-commit "$sha" >/dev/null 2>&1 || held+=("$sha")
    done < <(ck_git rev-list --reverse 'HEAD..@{upstream}' 2>/dev/null)
    if [ "${#held[@]}" -eq 0 ]; then rm -f "$KR/.last-sync-untrusted"; return 0; fi
    printf '%s\n' "${held[@]}" > "$KR/.last-sync-untrusted"
    ck_error "$1" "held back ${#held[@]} incoming commit(s) not signed by a device this machine trusts" \
        "review them with: corkern trust"
    return 1
}

# ck_track_upstream — make sure the branch tracks origin/<branch>. Returns 1 when the remote
# has no such branch yet (fresh, empty remote): nothing to integrate, the first push creates it.
ck_track_upstream() {
    ck_git rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1 && return 0
    local branch
    branch=$(ck_git branch --show-current 2>/dev/null) || return 1
    ck_git rev-parse --verify -q "refs/remotes/origin/$branch" >/dev/null 2>&1 || return 1
    ck_git branch -q --set-upstream-to="origin/$branch" >/dev/null 2>&1
}

ck_in_progress() {
    [ -d "$KR/.git/rebase-merge" ] || [ -d "$KR/.git/rebase-apply" ] || [ -f "$KR/.git/MERGE_HEAD" ]
}

# ck_commit_all MESSAGE — commit every tracked-content change (memory, registry, global
# instructions; sync state and secrets are excluded by .gitignore) so nothing blocks a rebase
ck_commit_all() {
    ck_commit_memory "$1" || true
    ck_git add -A >/dev/null 2>&1 || return 1
    ck_scan_staged || return 1
    ck_git diff --cached --quiet || ck_git commit -q -m \
        "$1: $(ck_git -c core.quotePath=false diff --cached --name-only | tr '\n' ' ')" >/dev/null 2>&1
}

ck_conflict_name() {  # ck_conflict_name PATH → sibling name for the local copy
    local f="$1" tag base
    tag="conflict-$(hostname -s 2>/dev/null || echo device)-$(date +%Y%m%d%H%M%S)"
    base=$(basename "$f")
    if [[ "$base" == *.* ]]; then echo "${f%.*}.$tag.${f##*.}"; else echo "$f.$tag"; fi
}

ck_merge_keep_both() {
    local hook="$1" rebase_out="$2" up f copy kept=()
    up=$(ck_git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null) || {
        ck_error "$hook" "git pull --rebase failed (no upstream to merge)" "$rebase_out"; return 1; }
    ck_commit_all "auto: sync" || true
    if ck_git merge --no-edit -q "$up" >/dev/null 2>&1; then ck_stamp pull; return 0; fi
    while IFS= read -r -d '' f; do
        if ck_git cat-file -e ":3:$f" 2>/dev/null; then
            ck_git show ":3:$f" > "$KR/$f"                       # remote version at the path
            if ck_git cat-file -e ":2:$f" 2>/dev/null; then       # both sides have it → keep ours too
                copy=$(ck_conflict_name "$f")
                ck_git show ":2:$f" > "$KR/$copy"
                ck_git add -- "$copy"
                kept+=("$copy")
            fi
        else
            ck_git show ":2:$f" > "$KR/$f"                       # remote deleted it → keep ours
        fi
        ck_git add -- "$f"
    done < <(ck_git diff --name-only -z --diff-filter=U)
    if [ -n "$(ck_git diff --name-only --diff-filter=U)" ] \
        || ! ck_git commit -q --no-edit >/dev/null 2>&1; then
        ck_git merge --abort >/dev/null 2>&1
        ck_error "$hook" "pull failed: rebase and merge both conflicted (repo restored)" "$rebase_out"
        return 1
    fi
    if [ "${#kept[@]}" -gt 0 ]; then
        printf '%s kept local copy: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${kept[@]}" >> "$KR/.last-sync-conflicts"
    fi
    ck_stamp pull
    return 0
}

# ck_push HOOK — push; on rejection rebase once and retry (another device pushed first)
ck_push() {
    local out
    if ! ck_git rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1; then
        # first push to a fresh remote: create the branch and track it
        if out=$(ck_git push -q -u origin HEAD 2>&1); then ck_stamp push; return 0; fi
        ck_error "$1" "first push to the remote failed" "$out"; return 1
    fi
    if out=$(ck_git push -q 2>&1); then ck_stamp push; return 0; fi
    ck_pull "$1" || return 1
    if out=$(ck_git push -q 2>&1); then ck_stamp push; return 0; fi
    ck_error "$1" "git push failed after retry" "$out"
    return 1
}

ck_ahead() {
    local n
    n=$(ck_git rev-list --count '@{upstream}..HEAD' 2>/dev/null) || return 0  # no upstream yet → try
    [ "$n" -gt 0 ]
}

# ck_bg FUNCTION — run in the background (detached, quiet); CORKERN_HOOK_FOREGROUND=1 for tests
ck_bg() {
    if [ -n "${CORKERN_HOOK_FOREGROUND:-}" ]; then "$@"; return; fi
    ( exec 9>&- ; "$@" ) </dev/null >/dev/null 2>&1 &
}
