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
# shellcheck source=migrate/lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"
parse_common "$@"
REPO="" REMOTE="" CODE_REPO="${CORKERN_CODE_REPO:-git@github.com:scpg/corkern.git}" CODE_REF="${CORKERN_CODE_REF:-main}"
set -- "${ARGS[@]+"${ARGS[@]}"}"
while [ $# -gt 0 ]; do case "$1" in
    --repo) REPO="$2"; shift ;; --remote) REMOTE="$2"; shift ;; --code-repo) CODE_REPO="$2"; shift ;;
    *) die "unknown option: $1" ;; esac; shift; done
STEPS=(final-v1-sync final-export backup retire-v1 install keys carry-over confirm trust push init-device iwe verify)
if [ "$SHOW_STATUS" = 1 ]; then show_status "$SCRIPT_NAME" "${STEPS[@]}"; exit 0; fi
V1="$(v1_repo)"
RETIRED="$V1-v1-retired"
KR="$(kr)"
BACKUP="$MIG_DIR/backup"
if [ -n "$REPO" ]; then TARGET="git@github.com:$REPO.git"; else TARGET="$REMOTE"; fi
DATA_PATHS=(memory .devices global projects.conf .metrics.tsv)

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

carry_over() {
    local base end report="$MIG_DIR/carry-over-report.txt" stamp host copied=0 kept=0 merged=0 same=0
    base=$(grep '^v1_export_commit=' "$KR/.corkern-migration" 2>/dev/null | cut -d= -f2)
    end=$(getvar v1_end_commit)
    [ -n "$base" ] || { err "the knowledge repo has no .corkern-migration (was it made by 2-setup-new-machine.sh?)"; return 1; }
    git -C "$RETIRED" cat-file -e "$base^{commit}" 2>/dev/null \
        || { err "v1 here does not know the export point $base — run 'git pull' in $RETIRED, then this script again"; return 1; }
    stamp=$(date +%Y%m%d%H%M%S); host=$(hostname -s 2>/dev/null || hostname)
    : > "$report"
    # -z: names with spaces, unicode, quotes or tabs come through unquoted and intact
    while IFS= read -r -d '' status && IFS= read -r -d '' f; do
        local dest="$KR/$f"
        case "$status" in
            D) echo "kept (deleted on v1 since the export): $f" >> "$report"; continue ;;
        esac
        if [ ! -e "$dest" ] || git -C "$RETIRED" show "$base:$f" 2>/dev/null | cmp -s - "$dest"; then
            mkdir -p "$(dirname "$dest")"; git -C "$RETIRED" show "$end:$f" > "$dest"
            echo "copied: $f" >> "$report"; copied=$((copied + 1))
        elif git -C "$RETIRED" show "$end:$f" | cmp -s - "$dest"; then
            same=$((same + 1))
        elif [ "$(basename "$f")" = MEMORY.md ]; then
            git -C "$RETIRED" show "$end:$f" | while IFS= read -r line; do
                grep -qxF -- "$line" "$dest" || printf '%s\n' "$line" >> "$dest"
            done
            echo "combined lines: $f" >> "$report"; merged=$((merged + 1))
        else
            local base_name="${f##*/}" copy
            if [[ "$base_name" == *.* ]]; then copy="${f%.*}.conflict-v1-$host-$stamp.${f##*.}"; else copy="$f.conflict-v1-$host-$stamp"; fi
            git -C "$RETIRED" show "$end:$f" > "$KR/$copy"
            echo "BOTH CHANGED — v1 version kept next to it: $copy" >> "$report"; kept=$((kept + 1))
        fi
    done < <(git -C "$RETIRED" diff -z --name-status --no-renames "$base" "$end" -- memory global)
    local slug line added=0
    while IFS= read -r line; do
        slug="${line%%:*}"
        [[ -z "$slug" || "$slug" == \#* ]] && continue
        grep -q "^$slug:" "$KR/corkern.conf" || { printf '%s\n' "$line" >> "$KR/corkern.conf"; echo "project added: $slug" >> "$report"; added=$((added + 1)); }
    done < <(git -C "$RETIRED" show "$end:projects.conf")
    # this machine's own paths (v1 projects.local.conf → v2 corkern.conf.local, never committed)
    local localconf=0
    if [ -f "$RETIRED/projects.local.conf" ]; then
        touch "$KR/corkern.conf.local"
        while IFS= read -r line; do
            slug="${line%%:*}"
            [[ -z "$slug" || "$slug" == \#* ]] && continue
            grep -q "^$slug:" "$KR/corkern.conf.local" \
                || { printf '%s\n' "$line" >> "$KR/corkern.conf.local"; echo "local path kept: $line" >> "$report"; localconf=$((localconf + 1)); }
        done < "$RETIRED/projects.local.conf"
    fi
    git -C "$KR" add -A
    if ! git -C "$KR" diff --cached --quiet; then
        run git -C "$KR" commit -q -m "corkern: carry over v1 changes from $host since the export" || return 1
    fi
    cat "$report" | sed 's/^/    /' | tee -a "$LOG"
    ok "carried over: $copied copied, $merged index(es) combined, $kept kept side by side, $added project(s), $localconf local path(s), $same already identical"
    [ "$kept" -gt 0 ] && warn "$kept file(s) changed on both sides — merge each *.conflict-v1-* file into its original, then delete the copy"
    return 0
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
    printf '%s\n' "$out" | grep -q "different here" && say "  Differing files are expected (both machines kept working); the carry-over kept both versions where needed."
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
