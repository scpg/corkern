# shellcheck shell=bash
# Shared helpers for corkern v2 hooks.
#
# Contract: hooks never block or break Claude Code. Every failure path exits 0 and
# leaves a diagnostic — sync problems in $CK_STATE/last-sync-<hook>-error (v1 format),
# setup problems in $CORKERN_STATE/last-hook-problem — for `corkern status`.
#
# Per-machine sync state ($CK_STATE = <git dir>/corkern: lock, last-sync-*, held) never lives in
# the work tree: anyone who can push could commit a symlink at a work-tree path, and the hooks
# would write through it. Hooks never write to a work-tree path with a shell redirection.
# Hooks print nothing on stdout (SessionStart stdout would be injected into Claude's context).

CORKERN_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/corkern"
CK_FORMAT=1   # knowledge repo format these hooks understand (src/corkern/knowledge_repo.py FORMAT_VERSION)
# Never synced, whatever the (synced) .gitignore says — written to .git/info/exclude.
# Same list as src/corkern/knowledge_repo.py MACHINE_LOCAL (tests check they match).
CK_EXCLUDES=(corkern.conf.local .corkern.lock '.last-sync-*' '*.bak-*' .distill-prompt.md)
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
    local gd
    gd=$(git -C "$KR" rev-parse --absolute-git-dir 2>/dev/null) || { ck_note "$1" "$KR: git dir not found"; return 1; }
    CK_STATE="$gd/corkern"
    if [ -L "$CK_STATE" ] || ! mkdir -p -- "$CK_STATE" 2>/dev/null; then
        ck_note "$1" "$CK_STATE is not a usable directory"; return 1
    fi
    ck_harden "$gd"
    ck_migrate_state
    return 0
}

# ck_harden GIT-DIR — defence in depth, idempotent: links that reach the repo anyway are
# checked out as plain files, and per-machine files stay unsynced even if a teammate pushes a
# .gitignore without them
ck_harden() {
    local ex="$1/info/exclude" p
    [ "$(git -C "$KR" config --local --get core.symlinks 2>/dev/null)" = false ] \
        || git -C "$KR" config --local core.symlinks false 2>/dev/null
    [ -L "$ex" ] && return 0
    mkdir -p -- "$1/info" 2>/dev/null || return 0
    for p in "${CK_EXCLUDES[@]}"; do
        grep -qxF -- "$p" "$ex" 2>/dev/null || printf '%s\n' "$p" >> "$ex"
    done
}

# ck_migrate_state — one time: state an older corkern kept in the work tree moves into
# $CK_STATE. Only untracked regular files: a link is never followed (or moved), and a tracked
# file belongs to the repo (a pushed one is held back by the pull, not adopted as our state).
ck_migrate_state() {
    local f name dest
    for f in "$KR"/.corkern.lock "$KR"/.last-sync-*; do
        [ -f "$f" ] && [ ! -L "$f" ] || continue
        name=${f##*/}
        git -C "$KR" ls-files --error-unmatch -- "$name" >/dev/null 2>&1 && continue
        case "$name" in
            .corkern.lock) rm -f -- "$f"; continue ;;
            .last-sync-untrusted) dest="$CK_STATE/held" ;;
            *) dest="$CK_STATE/${name#.}" ;;
        esac
        if [ -e "$dest" ] || [ -L "$dest" ]; then rm -f -- "$f"; else mv -T -- "$f" "$dest" 2>/dev/null; fi
    done
}

ck_git() { git -C "$KR" "$@"; }

# ck_lock SECONDS — serialize git work across hooks and sessions (fd 9 held until exit)
ck_lock() { exec 9>"$CK_STATE/lock" && flock -w "${1:-10}" 9; }

ck_stamp() { date '+%Y-%m-%d %H:%M:%S' > "$CK_STATE/last-sync-$1"; rm -f -- "$CK_STATE/last-sync-$1-error"; }

# ck_redact_urls — strip user:password@ / token@ from URLs (stdin → stdout)
ck_redact_urls() { sed -E 's#([A-Za-z][A-Za-z0-9+.-]*://)[^/@[:space:]]*@#\1#g'; }

# ck_due WHAT SECONDS — true if the last WHAT (pull/push) is older than SECONDS (or never)
ck_due() {
    local f="$CK_STATE/last-sync-$1" ts
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
    } | ck_redact_urls > "$CK_STATE/last-sync-$hook-error"
}

# ck_preflight HOOK — can we talk to the remote right now?
ck_preflight() {
    if ! ck_git remote get-url origin >/dev/null 2>&1; then
        ck_error "$1" "no remote configured" "run: git -C $KR remote add origin <url>"; return 1
    fi
    if ck_in_progress; then
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
CK_SECRET_HINT='PRIVATE KEY|gh[pousr]_|github_pat_|sk-|AKIA|ASIA|xox[abprs]-|AIza|GOCSPX-|glpat-|npm_|passw|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key'

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

# ── Per-machine files never leave this machine ────────────────────────────────────────────
# CK_EXCLUDES are in .git/info/exclude, but git gives a .gitignore in the work tree priority: a
# pushed `!corkern.conf.local` would make `git add -A` stage this machine's own files. So staging
# decides: one already tracked is marked skip-worktree (never staged again; status names it), new
# ones are unstaged right after `git add`, and a commit that still holds one is refused.
# Matched by file name, any case, at any depth (knowledge_repo.is_machine_local is the twin).

# ck_is_local PATH — its file name is a per-machine name
ck_is_local() {
    local b="${1##*/}" p
    b="${b,,}"
    # shellcheck disable=SC2053  # the right side is a glob on purpose
    for p in "${CK_EXCLUDES[@]}"; do [[ "$b" == ${p,,} ]] && return 0; done
    return 1
}

# ck_local_specs — pathspecs for every per-machine name; no .gitignore can defeat them
ck_local_specs() { local p; for p in "${CK_EXCLUDES[@]}"; do printf ':(glob,icase)**/%s\n' "$p"; done; }

# ck_keep_local — before `git add`: per-machine files git already tracks are never staged again
ck_keep_local() {
    local f
    local -a specs=()
    mapfile -t specs < <(ck_local_specs)
    while IFS= read -r -d '' f; do
        ck_git update-index --skip-worktree -- "$f" >/dev/null 2>&1
    done < <(ck_git ls-files -z -- "${specs[@]}" 2>/dev/null)
}

# ck_unstage_local — after `git add`: unstage per-machine files; if one is still staged, unstage
# everything and refuse (returns 1, nothing committed, reported)
ck_unstage_local() {
    local f
    local -a specs=() bad=()
    mapfile -t specs < <(ck_local_specs)
    ck_git reset -q -- "${specs[@]}" >/dev/null 2>&1
    while IFS= read -r -d '' f; do ck_is_local "$f" && bad+=("$f"); done \
        < <(ck_git diff --cached --name-only --no-renames -z 2>/dev/null)
    [ "${#bad[@]}" -eq 0 ] && return 0
    ck_git reset -q >/dev/null 2>&1
    ck_note commit "not committed: per-machine file(s) staged: ${bad[*]} (a .gitignore re-includes them? see: corkern status)"
    return 1
}

# ck_reincludes BLOB — prints the first `!` line of a .gitignore blob that would re-include a
# per-machine name (`!corkern.conf.local`, `!*.bak-*`, `!*`, `!memory/**` …); returns 1 if none.
# An unreadable blob counts as re-including (fail closed).
ck_reincludes() {
    local text line pat probe
    text=$(ck_git cat-file blob "$1" 2>/dev/null) || { printf 'unreadable'; return 0; }
    while IFS= read -r line; do
        line="${line%$'\r'}"
        [[ "$line" == '!'* ]] || continue
        while [[ "$line" == *' ' && "$line" != *'\ ' ]]; do line="${line% }"; done  # as git reads it
        pat="${line#!}"; pat="${pat%/}"; pat="${pat##*/}"; pat="${pat,,}"
        [ -n "$pat" ] || continue
        ck_is_local "$pat" && { printf '%s' "$line"; return 0; }
        for probe in corkern.conf.local .corkern.lock .last-sync-pull x.bak-1 .distill-prompt.md; do
            # shellcheck disable=SC2053  # the right side is the .gitignore pattern
            [[ "$probe" == $pat ]] && { printf '%s' "$line"; return 0; }
        done
    done <<< "$text"
    return 1
}

# ck_commit_memory MESSAGE-PREFIX — stage memory/ (incl. new files) and commit with file names
ck_commit_memory() {
    ck_keep_local
    ck_git add -A -- memory/ 2>/dev/null || return 1
    ck_unstage_local || return 1
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
    local out tries up
    out=$(ck_git fetch -q 2>&1) || { ck_error "$1" "git fetch failed" "$out"; return 1; }
    ck_track_upstream || { ck_commit_all "auto: sync" || true; ck_stamp pull; return 0; }  # empty remote
    # Resolve once: what is inspected is exactly what gets integrated
    up=$(ck_git rev-parse --verify -q '@{upstream}^{commit}' 2>/dev/null) \
        || { ck_error "$1" "could not resolve the upstream branch"; return 1; }
    ck_verify_incoming "$1" "$up" || { ck_commit_all "auto: sync" || true; return 1; }
    for tries in 1 2 3; do
        ck_commit_all "auto: sync" || true
        if out=$(ck_git rebase -q --end-of-options "$up" 2>&1); then ck_stamp pull; return 0; fi
        if ck_in_progress; then                      # real conflict → keep both versions
            ck_git rebase --abort >/dev/null 2>&1
            ck_merge_keep_both "$1" "$out" "$up"
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

# ── Inspect before integrating ────────────────────────────────────────────────────────────
# Nothing from the remote reaches the work tree before it is inspected, and any error while
# inspecting holds EVERYTHING back (fail closed). Held commits are listed, one per line as
# "<sha><TAB><reason>", in $CK_STATE/held — shown by `corkern status`, `corkern trust` and the
# session context.
#
# 1. Content: the net change from the merge base to the upstream commit (rebase and merge only
#    ever check out that commit, never the ones in between) must not add or change a symlink,
#    a path named like corkern's per-machine state, or Claude Code configuration at the repo
#    root. Once the remote removes such a path, the range is integrated again.
# 2. Signatures, when this machine has a trust store (`corkern trust --enable`): the keys THIS
#    machine confirmed. It lives in .git/, so it is never synced — a commit that adds a key to
#    the synced .devices/allowed_signers cannot vouch for itself. A commit counts only with an
#    SSH signature that git rates exactly G whose key is in that store; OpenPGP and X.509 are
#    switched off (git would otherwise ask the user's GnuPG keyring). Everything else waits
#    until `corkern trust` accepts it (or its device).
#
# Revisions given to git are always preceded by --end-of-options (and followed by --).

# Git settings for signature checks: SSH against this machine's trust store, nothing else
ck_sig_config() {
    printf '%s\n' -c "gpg.ssh.allowedSignersFile=$1" -c gpg.ssh.program=ssh-keygen \
        -c gpg.openpgp.program=false -c gpg.x509.program=false
}

# ck_trusted_fps TRUST-FILE — SHA256 fingerprints of the keys in this machine's trust store
ck_trusted_fps() {
    local line key
    while IFS= read -r line; do
        key=$(awk '{for (i = 1; i < NF; i++) if ($i ~ /^(ssh-|ecdsa-|sk-)/) { print $i " " $(i + 1); exit }}' <<< "$line")
        [ -n "$key" ] && printf '%s\n' "$key" | ssh-keygen -lf - 2>/dev/null | awk '{print $2}'
    done < "$1"
}

# ck_dangerous MODE PATH — prints why an incoming MODE/PATH may not be checked out
ck_dangerous() {
    local mode="$1" path="$2" base="${2##*/}"
    if [ "$mode" = 120000 ]; then echo "symlink"; return 0; fi
    case "$base" in .corkern.lock|.last-sync-*) echo "named like corkern's per-machine state"; return 0 ;; esac
    case "$path" in .claude|.claude/*|.mcp.json) echo "Claude Code configuration"; return 0 ;; esac
    # this machine's own registry: what it lists counts as confirmed here (config.check_projects)
    case "${path,,}" in corkern.conf.local) echo "this machine's own file (corkern.conf.local)"; return 0 ;; esac
    return 1
}

ck_hold() {  # ck_hold HOOK MESSAGE LINES... — record held commits, report, return 1
    local hook="$1" msg="$2" tmp
    shift 2
    tmp=$(mktemp -- "$CK_STATE/held.XXXXXX") || { ck_error "$hook" "$msg"; return 1; }
    printf '%s\n' "$@" > "$tmp" && mv -T -f -- "$tmp" "$CK_STATE/held"
    ck_error "$hook" "$msg" "review them with: corkern trust"
    return 1
}

ck_verify_incoming() {
    local hook="$1" up="$2" trust="$CK_STATE/trusted_signers" accepted="$CK_STATE/accepted"
    local head base commits meta path mode why sha raw nsha line
    local -a held=()
    local fail="could not inspect incoming commits — nothing integrated"
    head=$(ck_git rev-parse --verify -q 'HEAD^{commit}' 2>/dev/null) \
        || { ck_hold "$hook" "$fail" "ERROR"$'\t'"could not inspect incoming commits (no HEAD)"; return 1; }
    base=$(ck_git merge-base --end-of-options "$head" "$up" 2>/dev/null) \
        || { ck_hold "$hook" "$fail" "ERROR"$'\t'"could not inspect incoming commits (no merge base)"; return 1; }
    commits=$(ck_git rev-list --reverse --end-of-options "$head..$up" -- 2>/dev/null) \
        || { ck_hold "$hook" "$fail" "ERROR"$'\t'"could not inspect incoming commits (rev-list failed)"; return 1; }
    raw=$(mktemp -- "$CK_STATE/incoming.XXXXXX") \
        || { ck_hold "$hook" "$fail" "ERROR"$'\t'"could not inspect incoming commits (no temp file)"; return 1; }
    if ! ck_git diff --raw -z --no-renames --no-ext-diff --no-textconv --end-of-options "$base" "$up" -- > "$raw" 2>/dev/null; then
        rm -f -- "$raw"
        ck_hold "$hook" "$fail" "ERROR"$'\t'"could not inspect incoming commits (diff failed)"; return 1
    fi
    while IFS= read -r -d '' meta && IFS= read -r -d '' path; do
        [ "${meta##* }" = D ] && continue                       # removing something is always fine
        mode=${meta#:}; mode=${mode#* }; mode=${mode%% *}       # ":old new osha nsha S" → new
        if ! why=$(ck_dangerous "$mode" "$path"); then
            [ "$(printf '%s' "${path##*/}" | tr '[:upper:]' '[:lower:]')" = .gitignore ] || continue
            nsha=${meta#:}; nsha=${nsha#* * * }; nsha=${nsha%% *}  # ":old new osha nsha S" → nsha
            line=$(ck_reincludes "$nsha") || continue
            line=$(printf '%s' "$line" | tr -c '[:print:]' '?' | cut -c1-80)   # a teammate's text: data
            why="re-includes this machine's own files ($line)"
        fi
        sha=$(ck_git log -1 --format=%H --end-of-options "$head..$up" -- "$path" 2>/dev/null)
        held+=("${sha:-$up}"$'\t'"$why: $path")
    done < "$raw"
    rm -f -- "$raw"
    if [ -f "$trust" ]; then
        local fps sig
        local -a cfg=()
        fps=$(ck_trusted_fps "$trust")                   # empty → nothing passes (fail closed)
        mapfile -t cfg < <(ck_sig_config "$trust")
        while IFS= read -r sha; do
            [ -n "$sha" ] || continue
            [ -f "$accepted" ] && grep -qx -- "$sha" "$accepted" && continue
            sig=$(ck_git "${cfg[@]}" log -1 --format='%G?%x09%GK' --end-of-options "$sha" -- 2>/dev/null)
            if [ "${sig%%$'\t'*}" = G ] && [ -n "$fps" ] && grep -qxF -- "${sig#*$'\t'}" <<< "$fps"; then continue; fi
            held+=("$sha"$'\t'"not signed by a device this machine trusts")
        done <<< "$commits"
    fi
    if [ "${#held[@]}" -eq 0 ]; then rm -f -- "$CK_STATE/held"; return 0; fi
    ck_hold "$hook" "held back incoming commits (${#held[@]} reason(s)) — nothing integrated" "${held[@]}"
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
# instructions; never a per-machine file, see ck_unstage_local) so nothing blocks a rebase
ck_commit_all() {
    ck_commit_memory "$1" || true
    ck_keep_local
    ck_git add -A >/dev/null 2>&1 || return 1
    ck_unstage_local || return 1
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

# ck_put BLOB REL — write a blob to a work-tree path without following links: refused if a
# folder on the way is a symlink; written to a temp file next to it, then renamed over the path
# (a link there is replaced, never followed)
ck_put() {
    local blob="$1" rel="$2" dir c part tmp
    dir=$(dirname -- "$rel")
    c="$KR"
    if [ "$dir" != . ]; then
        IFS=/ read -ra parts <<< "$dir"
        for part in "${parts[@]}"; do
            c="$c/$part"
            [ -L "$c" ] && return 1
        done
    fi
    mkdir -p -- "$KR/$dir" || return 1
    tmp=$(mktemp -- "$KR/$dir/.corkern-put.XXXXXX") || return 1
    if ! ck_git show "$blob" > "$tmp" || ! chmod "$(printf '%o' $(( 0666 & ~0$(umask) )))" -- "$tmp"; then
        rm -f -- "$tmp"; return 1
    fi
    mv -T -f -- "$tmp" "$KR/$rel"
}

ck_merge_keep_both() {
    local hook="$1" rebase_out="$2" up="$3" f copy ok=1 kept=()
    ck_commit_all "auto: sync" || true
    if ck_git merge --no-edit -q --end-of-options "$up" >/dev/null 2>&1; then ck_stamp pull; return 0; fi
    while IFS= read -r -d '' f; do
        ck_is_local "$f" && { ok=0; break; }                      # never a per-machine file
        if ck_git cat-file -e ":3:$f" 2>/dev/null; then
            ck_put ":3:$f" "$f" || { ok=0; break; }              # remote version at the path
            if ck_git cat-file -e ":2:$f" 2>/dev/null; then       # both sides have it → keep ours too
                copy=$(ck_conflict_name "$f")
                ck_put ":2:$f" "$copy" || { ok=0; break; }
                ck_git add -- "$copy"
                kept+=("$copy")
            fi
        else
            ck_put ":2:$f" "$f" || { ok=0; break; }              # remote deleted it → keep ours
        fi
        ck_git add -- "$f"
    done < <(ck_git diff --name-only -z --diff-filter=U)
    if [ "$ok" = 0 ] || [ -n "$(ck_git diff --name-only --diff-filter=U)" ] \
        || ! ck_git commit -q --no-edit >/dev/null 2>&1; then
        ck_git merge --abort >/dev/null 2>&1
        ck_error "$hook" "pull failed: rebase and merge both conflicted (repo restored)" "$rebase_out"
        return 1
    fi
    if [ "${#kept[@]}" -gt 0 ]; then
        printf '%s kept local copy: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${kept[@]}" >> "$CK_STATE/last-sync-conflicts"
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
    n=$(ck_git rev-list --count --end-of-options '@{upstream}..HEAD' -- 2>/dev/null) || return 0  # no upstream yet → try
    [ "$n" -gt 0 ]
}

# ck_bg FUNCTION — run in the background (detached, quiet); CORKERN_HOOK_FOREGROUND=1 for tests
ck_bg() {
    if [ -n "${CORKERN_HOOK_FOREGROUND:-}" ]; then "$@"; return; fi
    ( exec 9>&- ; "$@" ) </dev/null >/dev/null 2>&1 &
}
