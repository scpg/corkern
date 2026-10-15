#!/usr/bin/env bash
# Step 3 of the v1 → v2 migration — run on each machine that still runs corkern v1, once v2
# has proven itself on the new machine. Close all Claude Code sessions first.
#
#   bash migrate/3-switch-v1-machine.sh --repo <owner>/<name>   (the knowledge repo from step 2)
#   bash migrate/3-switch-v1-machine.sh --remote <git-url>
#   options: --code-repo URL, --yes, --dry-run, --status
#
# Final v1 sync, a backup of everything v1 wired into Claude Code, then v1 is retired
# (links removed, repo renamed to ~/dev/corkern-v1-retired — nothing deleted). v2 is installed
# from the knowledge repo and everything v1 wrote since the export is carried over: files only v1
# changed are copied, files both sides changed are kept side by side (<name>.conflict-v1-...),
# MEMORY.md lines are combined, nothing is deleted. Then keys, trust, Claude Code wiring, IWE
# and a sync check. rollback-v1-machine.sh undoes all of it.
#
# The cloned knowledge repo is not trusted: the export point it names is verified against this
# machine's own record and v1's history before v1 is touched, the carry-over never writes through
# a symlink (temp file + rename, as the hooks), and everything it commits goes through the hooks'
# secret scan; the push step only runs after a scan of everything outgoing.
# shellcheck source=migrate/lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"
parse_common "$@"
REPO="" REMOTE="" CODE_REPO="${CORKERN_CODE_REPO:-git@github.com:scpg/corkern.git}" CODE_REF="${CORKERN_CODE_REF:-main}"
set -- "${ARGS[@]+"${ARGS[@]}"}"
while [ $# -gt 0 ]; do case "$1" in
    --repo) REPO="$2"; shift ;; --remote) REMOTE="$2"; shift ;; --code-repo) CODE_REPO="$2"; shift ;;
    *) die "unknown option: $1" ;; esac; shift; done
STEPS=(final-v1-sync final-export backup retire-v1 install keys carry-over confirm trust scan push init-device iwe verify)
if [ "$SHOW_STATUS" = 1 ]; then show_status "$SCRIPT_NAME" "${STEPS[@]}"; exit 0; fi
V1="$(v1_repo)"
RETIRED="$V1-v1-retired"
KR="$(kr)"
BACKUP="$MIG_DIR/backup"
if [ -n "$REPO" ]; then TARGET="git@github.com:$REPO.git"; else TARGET="$REMOTE"; fi
DATA_PATHS=(memory .devices global projects.conf .metrics.tsv)

# ── the export point: the v1 commit the knowledge repo was built from ─────────────────────
# Script 1's own record on this machine (its state, or the manifest of the folder it packed) wins;
# the synced .corkern-migration can be written by anyone who can push, so it is only a claim.
local_export_point() {
    local v
    v=$(grep '^var:v1_export_commit=' "$MIG_DIR/1-export-v1.state" 2>/dev/null | tail -1 | cut -d= -f2-)
    [ -n "$v" ] || v=$(grep '^v1_export_commit=' "$HERE/manifest.txt" 2>/dev/null | head -1 | cut -d= -f2-)
    printf '%s' "$v"
}
synced_export_point() {   # read from the commit, never through the work tree
    local src="$KR" tmp="" v
    if [ ! -d "$KR/.git" ]; then
        tmp=$(mktemp -d) || return 1
        timeout 60 git clone -q --bare --depth 1 -- "$TARGET" "$tmp/kr.git" >> "$LOG" 2>&1 || { rm -rf -- "$tmp"; return 1; }
        src="$tmp/kr.git"
    fi
    v=$(git -C "$src" show HEAD:.corkern-migration 2>/dev/null | grep '^v1_export_commit=' | head -1 | cut -d= -f2-)
    [ -z "$tmp" ] || rm -rf -- "$tmp"
    printf '%s' "$v"
}
# export_point_ok REPO TIP... — sets BASE when the export point is a commit id this v1 has and an
# ancestor of one of the TIPs; otherwise BASE_WHY says why (and how to fix it)
export_point_ok() {
    local repo="$1" mine synced tip; shift
    BASE="" BASE_WHY=""
    mine=$(local_export_point); synced=$(synced_export_point)
    if [ -n "$mine" ] && [ -n "$synced" ] && [ "$mine" != "$synced" ]; then
        BASE_WHY="export point differs: script 1 recorded $mine on this machine, the knowledge repo's .corkern-migration says $synced — find out who changed it before going on"
        return 1
    fi
    BASE="${mine:-$synced}"
    if [ -z "$BASE" ]; then BASE_WHY="the knowledge repo has no .corkern-migration (was it made by 2-setup-new-machine.sh?)"
    elif ! [[ "$BASE" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then BASE_WHY="the export point '$BASE' in .corkern-migration is not a commit id"
    elif ! git -C "$repo" cat-file -e "$BASE^{commit}" 2>/dev/null; then
        BASE_WHY="this v1 does not have the export point $BASE — fetch v1 first (git -C $repo pull), then run this script again"
    else
        for tip in "$@"; do
            git -C "$repo" merge-base --is-ancestor --end-of-options "$BASE" "$tip" 2>/dev/null && return 0
        done
        BASE_WHY="the export point $BASE is not an ancestor of v1's history here — this knowledge repo was not made from this v1"
    fi
    [ -z "$BASE_WHY" ] || { BASE=""; return 1; }
}

title "corkern migration — 3/3: switch this machine from v1 to v2 ($(hostname))"
say "  log: $LOG"
title "Preconditions"
if done_step retire-v1; then ok "v1 already retired by an earlier run ($RETIRED)"
else
    need "v1 installed here ($V1)" "nothing to switch — for a fresh machine use 2-setup-new-machine.sh" test -f "$V1/projects.conf"
    need "$RETIRED does not exist yet" "move it away first" bash -c "! [ -e '$RETIRED' ]"
    [ -d "$V1/.git" ] && v1_state_needs "$V1"
    v1_stray_needs "$V1"
fi
need "no Claude Code session running" "close all Claude Code sessions (their hooks would run while v1 is unwired)" \
     bash -c "! pgrep -u $(id -u) -x claude"
need "where the knowledge repo is: --repo <owner>/<name> or --remote <url>" "add one of the two options" test -n "$TARGET"
need "knowledge repo reachable: $TARGET" "check access (ssh -T git@github.com) and the name" timeout 20 git ls-remote "$TARGET"
if done_step install; then ok "v2 already installed by an earlier run ($KR)"
else need "no v2 knowledge repo yet at $KR" "move it away, or set CORKERN_MEMORY_DIR" bash -c "! [ -e '$KR' ]"; fi
if ! done_step carry-over && [ "$FAILED_NEEDS" = 0 ]; then
    if done_step retire-v1; then BASE_REPO="$RETIRED"; else BASE_REPO="$V1"; fi
    export_point_ok "$BASE_REPO" HEAD "@{upstream}"
    need "export point verified${BASE:+: $BASE}" "$BASE_WHY" test -n "$BASE"
fi
for t in git uv ssh-keygen flock timeout python3; do need "$t installed" "install $t" command -v "$t"; done
need "python3 >= 3.10" "install python3 3.10 or newer" py_ok
need "corkern code reachable: $CODE_REPO" "check GitHub access or pass --code-repo" timeout 20 git ls-remote --quiet "$CODE_REPO" "$CODE_REF"
[ -n "$REPO" ] && need "gh (GitHub CLI) logged in" "gh auth login" gh auth status
# shellcheck disable=SC2016  # expanded by the inner bash
warn_if_not "CORKERN not set in the environment" "remove 'export CORKERN=…' from your shell profile after the switch" bash -c '[ -z "${CORKERN:-}" ]'
end_preflight

final_v1_sync() {
    local conflicts; conflicts=$(v1_conflicts "$V1")
    if [ -n "$conflicts" ]; then err "half-merged / conflict markers: $(echo "$conflicts" | tr '\n' ' ')— nothing committed"; return 1; fi
    local pending; pending=$(git -C "$V1" status --porcelain -- "${DATA_PATHS[@]}")
    if [ -n "$pending" ]; then
        printf '%s\n' "$pending" | sed 's/^/      /' | tee -a "$LOG"
        ask "Commit this last v1 data so it is carried over?" || { err "not committed"; return 1; }
        run git -C "$V1" add -A -- "${DATA_PATHS[@]}" && run git -C "$V1" commit -q -m "memory: final v1 sync before v2" || return 1
    fi
    v1_integrate || return 1
    setvar v1_end_commit "$(git -C "$V1" rev-parse HEAD)"
    ok "v1 is at $(getvar v1_end_commit)"
}

final_export() {
    if [ ! -f "$V1/export-import.py" ]; then
        warn "this v1 has no 'corkern export' yet (git pull in $V1) — no final export; the carry-over still runs"
        return 0
    fi
    run "$V1/corkern" export --out "$MIG_DIR/v1-final-export.tar" || return 1
    setvar final_export "$MIG_DIR/v1-final-export.tar"
    ok "everything v1 holds here is in $MIG_DIR/v1-final-export.tar (also your backup)"
}

backup() {
    mkdir -p "$BACKUP"
    [ -f "$HOME/.claude/settings.json" ] && cp -p "$HOME/.claude/settings.json" "$BACKUP/settings.json"
    : > "$BACKUP/links.tsv"
    local l
    for l in "$HOME/.claude/CLAUDE.md" "$HOME/.claude/corkern.sh" "$HOME/.local/bin/corkern" \
             "$HOME/.local/bin/corkern-trace" "$HOME"/.claude/commands/corkern-*.md "$HOME"/.claude/projects/*/memory; do
        [ -L "$l" ] && [[ "$(readlink "$l")" == "$V1"/* ]] && printf '%s\t%s\n' "$l" "$(readlink "$l")" >> "$BACKUP/links.tsv"
    done
    # `claude mcp get` exits non-zero when the server cannot start — the registration still exists
    if command -v claude >/dev/null; then claude mcp get iwe > "$BACKUP/iwe-mcp.txt" 2>/dev/null || true; fi
    grep -q "Command: *$V1" "$BACKUP/iwe-mcp.txt" 2>/dev/null || rm -f "$BACKUP/iwe-mcp.txt"
    ok "backup in $BACKUP: settings.json, $(wc -l < "$BACKUP/links.tsv") v1 link(s)$([ -f "$BACKUP/iwe-mcp.txt" ] && echo ", v1's IWE server")"
}

retire_v1() {
    say "  v1 will be retired (reversible with rollback-v1-machine.sh):"
    say "    - $(wc -l < "$BACKUP/links.tsv") link(s) into v1 removed (Claude Code settings stay until init-device)"
    [ -f "$BACKUP/iwe-mcp.txt" ] && say "    - v1's 'iwe' MCP server unregistered"
    say "    - $V1 renamed to $RETIRED (kept, not deleted)"
    ask "Retire v1 now?" || { err "v1 not retired"; return 1; }
    if [ -f "$BACKUP/iwe-mcp.txt" ]; then run claude mcp remove iwe --scope user || return 1; fi
    cut -f1 "$BACKUP/links.tsv" | while IFS= read -r l; do rm -f -- "$l"; done
    run mv "$V1" "$RETIRED" || return 1
    ok "v1 retired"
}

install() {
    local src="$MIG_DIR/code"
    rm -rf "$src"
    run git clone -q --branch "$CODE_REF" -- "$CODE_REPO" "$src" || return 1
    CORKERN_CODE_REPO="$CODE_REPO" CORKERN_CODE_REF="$CODE_REF" \
        run bash "$src/install.sh" --clone "$TARGET" --memory-dir "$KR" || return 1
    v2_cli && test -f "$KR/corkern.conf"
}

keys() {
    ask "Create this machine's signing key$([ -n "$REPO" ] && echo " and push (deploy) key")?" \
        || { warn "skipped — run later: corkern signing"; return 0; }
    if [ -n "$REPO" ]; then run c2 signing --yes --register-github --deploy-key || return 1
    else run c2 signing --yes < /dev/null || return 1; fi
    return 0
}

# ── writing into the knowledge repo: never through a symlink ───────────────────────────────
# kr_unsafe REL — why REL must not be written (a symlink or a non-folder on the way, the file
# itself a symlink, or a path outside the repo); prints nothing when it is safe
kr_unsafe() {
    local rest="$1" c="$KR" part
    while :; do
        part="${rest%%/*}"; c="$c/$part"
        [ -L "$c" ] && { echo "symlink: ${c#"$KR"/}"; return; }
        [ "$part" = "$rest" ] && break
        [ -e "$c" ] && [ ! -d "$c" ] && { echo "not a folder: ${c#"$KR"/}"; return; }
        rest="${rest#*/}"
    done
    case "$(realpath -m -- "$KR/$1")" in "$(realpath -- "$KR")"/*) ;; *) echo "outside the knowledge repo" ;; esac
}
# kr_put REL COMMAND... — COMMAND's output becomes REL: written to a temp file next to it, then
# renamed over it (as the hooks' ck_put); refused when kr_unsafe objects
kr_put() {
    local rel="$1" dir tmp; shift
    [ -z "$(kr_unsafe "$rel")" ] || return 1
    dir=$(dirname -- "$KR/$rel")
    mkdir -p -- "$dir" || return 1
    tmp=$(mktemp -- "$dir/.corkern-put.XXXXXX") || return 1
    if ! "$@" > "$tmp" || ! chmod "$(printf '%o' $(( 0666 & ~0$(umask) )))" -- "$tmp"; then rm -f -- "$tmp"; return 1; fi
    mv -T -f -- "$tmp" "$KR/$rel"
}
# with_lines REL LINE... — REL's text (a missing final newline added), then each LINE
with_lines() {
    local rel="$1"; shift
    if [ -s "$KR/$rel" ]; then cat -- "$KR/$rel"; [ -z "$(tail -c1 -- "$KR/$rel")" ] || echo; fi
    [ $# -eq 0 ] || printf '%s\n' "$@"
}
# has_line FILE LINE — LINE is a whole line of FILE (the last one may lack its newline)
has_line() { [ -f "$1" ] && grep -qxF -- "$2" "$1"; }
has_slug() { [ -f "$1" ] && cut -d: -f1 -- "$1" | grep -qxF -- "$2"; }

# scan_staged REPORT — the hooks' secret scan on everything staged in the knowledge repo: a secret
# is replaced by a marker (the original stays in this machine's quarantine), a file corkern cannot
# check is left out (reported). If the scan cannot run nothing stays staged and it fails (closed).
scan_staged() {
    local report="$1" f out hint
    local -a staged=() suspect=() redacted=()
    while IFS= read -r -d '' f; do staged+=("$f"); done \
        < <(git -C "$KR" diff --cached --name-only --no-renames -z --diff-filter=AM)
    [ "${#staged[@]}" -gt 0 ] || return 0
    hint=$(sed -n "s/^CK_SECRET_HINT='\(.*\)'$/\1/p" "$HOME/.local/share/corkern/hooks/lib.sh" 2>/dev/null)
    if [ -n "$hint" ]; then
        while IFS= read -r -d '' f; do suspect+=("$f"); done < <(cd "$KR" && grep -lZiE -- "$hint" "${staged[@]}" 2>/dev/null)
        [ "${#suspect[@]}" -gt 0 ] || return 0
    fi
    if [ -z "$hint" ] || ! out=$(cd "$KR" && c2 secrets --redact "${suspect[@]}" 2>> "$LOG"); then
        git -C "$KR" reset -q
        err "the secret scan could not run — nothing committed; run this script again (it resumes here)"
        return 1
    fi
    while IFS= read -r f; do
        case "$f" in
            "") ;;
            "HOLD "*) git -C "$KR" reset -q -- "${f#HOLD }"
                      echo "not committed — could not be checked for secrets (binary or over 2 MB): ${f#HOLD }" >> "$report" ;;
            *) redacted+=("$f"); echo "secret removed (original kept on this machine only): $f" >> "$report" ;;
        esac
    done <<< "$out"
    [ "${#redacted[@]}" -eq 0 ] || git -C "$KR" add -- "${redacted[@]}"
}

carry_over() {
    local base end report="$MIG_DIR/carry-over-report.txt" stamp host copied=0 kept=0 merged=0 same=0 skipped=0 lost=0
    end=$(getvar v1_end_commit)
    [[ "$end" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || { err "no v1 end commit recorded (step final-v1-sync)"; return 1; }
    export_point_ok "$RETIRED" "$end" || { err "$BASE_WHY"; return 1; }
    base="$BASE"
    stamp=$(date +%Y%m%d%H%M%S); host=$(hostname -s 2>/dev/null || hostname)
    : > "$report"
    # -z: names with spaces, unicode, quotes or tabs come through unquoted and intact
    local status f why line slug
    while IFS= read -r -d '' status && IFS= read -r -d '' f; do
        local dest="$KR/$f"
        case "$status" in
            D) echo "kept (deleted on v1 since the export): $f" >> "$report"; continue ;;
        esac
        why=$(kr_unsafe "$f")
        if [ -n "$why" ]; then
            echo "skipped ($why): $f — v1's version stays in $RETIRED" >> "$report"; skipped=$((skipped + 1)); continue
        fi
        if [ ! -e "$dest" ] || git -C "$RETIRED" show "$base:$f" 2>/dev/null | cmp -s - "$dest"; then
            kr_put "$f" git -C "$RETIRED" show "$end:$f" || { echo "skipped (could not write): $f" >> "$report"; skipped=$((skipped + 1)); continue; }
            echo "copied: $f" >> "$report"; copied=$((copied + 1))
        elif git -C "$RETIRED" show "$end:$f" | cmp -s - "$dest"; then
            same=$((same + 1))
        elif [ "$(basename "$f")" = MEMORY.md ]; then
            local -a add=() want=()
            local -A seen=()
            while IFS= read -r line || [ -n "$line" ]; do
                want+=("$line")
                has_line "$dest" "$line" || [ -n "${seen["x$line"]:-}" ] || { add+=("$line"); seen["x$line"]=1; }
            done < <(git -C "$RETIRED" show "$end:$f")
            [ "${#add[@]}" -eq 0 ] || kr_put "$f" with_lines "$f" "${add[@]}" || true
            local missing=0
            for line in "${want[@]+"${want[@]}"}"; do has_line "$dest" "$line" || missing=$((missing + 1)); done
            if [ "$missing" -eq 0 ]; then echo "combined lines: $f" >> "$report"; merged=$((merged + 1))
            else echo "NOT combined — $missing line(s) of v1's version missing: $f" >> "$report"; lost=$((lost + 1)); fi
        else
            local base_name="${f##*/}" copy
            if [[ "$base_name" == *.* ]]; then copy="${f%.*}.conflict-v1-$host-$stamp.${f##*.}"; else copy="$f.conflict-v1-$host-$stamp"; fi
            kr_put "$copy" git -C "$RETIRED" show "$end:$f" || { echo "skipped (could not write): $copy" >> "$report"; skipped=$((skipped + 1)); continue; }
            echo "BOTH CHANGED — v1 version kept next to it: $copy" >> "$report"; kept=$((kept + 1))
        fi
    done < <(git -C "$RETIRED" diff -z --name-status --no-renames "$base" "$end" -- memory global)
    # registrations: v1 projects.conf → corkern.conf; this machine's own paths (v1
    # projects.local.conf → v2 corkern.conf.local, never committed). A last line without a newline
    # counts, and the destination gets one before anything is appended.
    local added=0 localconf=0 src rel n
    for src in projects.conf projects.local.conf; do
        if [ "$src" = projects.conf ]; then rel=corkern.conf; else rel=corkern.conf.local; fi
        [ "$src" = projects.conf ] || [ -f "$RETIRED/$src" ] || continue
        why=$(kr_unsafe "$rel")
        if [ -n "$why" ]; then echo "skipped ($why): $rel — v1's $src stays in $RETIRED" >> "$report"; skipped=$((skipped + 1)); continue; fi
        local -a add=()
        local -A slugs=()
        while IFS= read -r line || [ -n "$line" ]; do
            slug="${line%%:*}"
            [[ -z "$slug" || "$slug" == \#* ]] && continue
            has_slug "$KR/$rel" "$slug" || [ -n "${slugs["x$slug"]:-}" ] && continue
            add+=("$line"); slugs["x$slug"]=1
        done < <(if [ "$src" = projects.conf ]; then git -C "$RETIRED" show "$end:projects.conf"; else cat -- "$RETIRED/$src"; fi)
        [ "${#add[@]}" -eq 0 ] && continue
        kr_put "$rel" with_lines "$rel" "${add[@]}" || true
        n=0
        for line in "${add[@]}"; do
            if has_line "$KR/$rel" "$line"; then
                n=$((n + 1))
                if [ "$rel" = corkern.conf ]; then echo "project added: ${line%%:*}" >> "$report"; else echo "local path kept: $line" >> "$report"; fi
            else echo "NOT added to $rel: $line" >> "$report"; lost=$((lost + 1)); fi
        done
        if [ "$rel" = corkern.conf ]; then added=$n; else localconf=$n; fi
    done
    git -C "$KR" add -A
    scan_staged "$report" || return 1
    if ! git -C "$KR" diff --cached --quiet; then
        run git -C "$KR" commit -q -m "corkern: carry over v1 changes from $host since the export" || return 1
    fi
    # corkern.conf is shared, so harvest / init-device use a project's path only once this machine
    # confirmed it: this machine's own v1 registry confirms the slugs whose path is the same here
    git -C "$RETIRED" show "$end:projects.conf" | c2 add --confirm-listed >> "$report" 2>&1 \
        || echo "projects NOT confirmed on this machine — run: corkern add --confirm-all" >> "$report"
    sed 's/^/    /' "$report" | tee -a "$LOG"
    setvar carried $((copied + merged + kept + added + localconf))
    if [ "$(getvar carried)" = 0 ] && [ "$skipped" = 0 ]; then ok "nothing to carry over — v1 wrote nothing since the export ($same already identical)"
    else ok "carried over: $copied copied, $merged index(es) combined, $kept kept side by side, $added project(s), $localconf local path(s), $same already identical"; fi
    [ "$kept" -gt 0 ] && warn "$kept file(s) changed on both sides — merge each *.conflict-v1-* file into its original, then delete the copy"
    [ "$skipped" -gt 0 ] && warn "$skipped file(s) skipped (see the report above) — v1's versions are in $RETIRED; copy what you need by hand"
    [ "$lost" -eq 0 ] || { err "$lost line(s) did not arrive (see the report above) — nothing is lost: v1 keeps them in $RETIRED"; return 1; }
    return 0
}

# scan_outgoing — before the push: anything left uncommitted goes through the same scan and is
# committed; then every commit that would be pushed is checked, and a secret in one stops the push
scan_outgoing() {
    local report="$MIG_DIR/scan-report.txt"
    : > "$report"
    git -C "$KR" add -A
    scan_staged "$report" || return 1
    git -C "$KR" diff --cached --quiet || run git -C "$KR" commit -q -m "corkern: changes from the switch on $(hostname -s 2>/dev/null || hostname)" || return 1
    sed 's/^/    /' "$report" | tee -a "$LOG"
    if ! run c2 secrets --scan --outgoing; then
        err "a commit that would be pushed holds a secret — nothing pushed. Remove it from those commits (they are not on the remote yet), then run this script again"
        return 1
    fi
    ok "nothing that will be pushed holds a secret"
}

confirm() {
    local f; f=$(getvar final_export)
    [ -n "$f" ] || { ok "no final export (older v1) — skipped"; return 0; }
    local out; out=$(c2 import "$f" --check 2>&1); printf '%s\n' "$out" >> "$LOG"
    printf '%s\n' "$out" | grep -E "identical|missing here|different here|not here" | sed 's/^/  /'
    if printf '%s\n' "$out" | grep -qE "missing here|not here"; then
        say "  Some of v1's data is not in v2 (it may also have been deleted on purpose on another machine)."
        ask "Import what is missing? (differing files stay as they are)" || { warn "not imported — check later: corkern import $f --check"; return 0; }
        run c2 import "$f" --apply --yes --missing-only || return 1
    fi
    if [ "$(getvar carried)" = 0 ]; then
        ok "nothing to carry over — v1 wrote nothing since the export"
        printf '%s\n' "$out" | grep -q "different here" \
            && warn "files differ although v1 wrote nothing since the export — check them: corkern import $f --check"
    else
        printf '%s\n' "$out" | grep -q "different here" && say "  Differing files are expected (both machines kept working); the carry-over kept both versions where needed."
    fi
    ok "confirmed against the final v1 export"
}

trust() {
    git -C "$KR" config --local --get user.signingKey >/dev/null || { warn "no signing key — trust skipped"; return 0; }
    c2 trust --enable < <(yes n 2>/dev/null) >> "$LOG" 2>&1 || return 1
    local fp name
    while read -r fp name; do
        if ask "Trust $name ($fp)? Check: 'corkern signing --show' on that machine shows this fingerprint"; then
            run c2 trust --device "$fp" --yes || return 1
        else warn "not trusted: $name — its memories will be held back here"; fi
    done < <(c2 trust | sed -n 's/^  ? \(SHA256:[^ ]*\)  *\(.*\)$/\1 \2/p')
    setvar my_fingerprint "$(c2 signing --show | sed -n 's/.*Fingerprint *: *\(SHA256:[^ ]*\).*/\1/p')"
    ok "trust set — this machine's key: $(getvar my_fingerprint)"
}

push() {
    hook corkern-push >> "$LOG" 2>&1
    [ "$(git -C "$KR" rev-parse HEAD)" = "$(git -C "$KR" ls-remote origin HEAD | cut -f1)" ] \
        || { err "could not push to the knowledge repo — see corkern status"; return 1; }
    ok "carry-over, keys and reminder are on the remote"
}

init_device() {
    run c2 init-device || return 1
    ask "Apply this (replaces v1's hooks; project memory, global CLAUDE.md, slash commands)?" \
        || { warn "skipped — run later: corkern init-device --apply"; return 0; }
    run c2 init-device --apply --yes
}

iwe_step() {
    command -v iwec >/dev/null || [ -x "$HOME/.cargo/bin/iwec" ] || { warn "IWE not installed — skipped"; return 0; }
    run c2 iwe --setup && run c2 iwe --install --yes
}

verify() {
    local out; out=$(c2 status -v 2>&1); printf '%s\n' "$out" >> "$LOG"
    printf '%s\n' "$out" | grep -E "✗|Hooks wired|problem\(s\)|All good" | sed 's/^/    /'
    if grep -q '\.claude/corkern\.sh' "$HOME/.claude/settings.json" 2>/dev/null; then
        warn "v1 hooks still in settings.json (init-device skipped?)"; fi
    hook corkern-push >> "$LOG" 2>&1
    [ "$(git -C "$KR" rev-parse HEAD)" = "$(git -C "$KR" ls-remote origin HEAD | cut -f1)" ] || { err "not in sync with the remote"; return 1; }
    ok "this machine runs corkern v2 and is in sync"
}

title "Steps"
step final-v1-sync "Last v1 sync (commit + push v1 data)"                 final_v1_sync
step final-export  "Final v1 export (backup + what v2 is checked against)" final_export
step backup        "Back up v1's Claude Code wiring"                       backup
step retire-v1     "Retire v1 (reversible)"                               retire_v1
STEP_FAIL_HINT="(v1 is retired meanwhile — to go back to it: bash $HERE/rollback-v1-machine.sh)"
step install       "Install corkern v2 from the knowledge repo"           install
step keys          "This machine's signing key (+ push key)"              keys
step carry-over    "Carry over what v1 wrote since the export"            carry_over
step confirm       "Confirm: everything v1 held is in v2"                 confirm
step trust         "Pull verification (trust your other machines)"        trust
step scan          "Secret scan of everything that will be pushed"        scan_outgoing
step push          "Push to the knowledge repo"                           push
step init-device   "Wire Claude Code (replaces v1's hooks)"               init_device
step iwe           "IWE knowledge graph (optional)"                       iwe_step
step verify        "Check everything"                                     verify

title "Done — this machine runs corkern v2"
say "  ${B}On your other v2 machines${N}, trust this one — until then they hold its memories back"
say "  (their session start says so):   corkern trust --device $(getvar my_fingerprint)"
say "  v1 is kept at $RETIRED. Undo everything: bash $HERE/rollback-v1-machine.sh"
say "  Carry-over report: $MIG_DIR/carry-over-report.txt"
say "  Open Claude Code in a project and check /memory, /hooks and the corkern box at session start."
say "  Log: $LOG"
