#!/usr/bin/env bash
# Step 2 of the v1 → v2 migration — run on a NEW machine (no corkern yet), in the folder that
# 1-export-v1.sh produced.
#
#   bash 2-setup-new-machine.sh --repo <owner>/<name>   publish to a new PRIVATE GitHub repo (gh)
#   bash 2-setup-new-machine.sh --remote <git-url>      or to an existing empty git remote
#   options: --code-repo URL (corkern code, default git@github.com:scpg/corkern.git, branch
#            corkern-v2), --yes, --dry-run, --status
#
# Installs corkern v2, builds the knowledge repo from the export, checks it for secrets,
# publishes it privately, sets up this machine's signing + push keys and trust, wires Claude
# Code (project memory, hooks, global CLAUDE.md, slash commands), IWE if installed, and proves
# that a sync round trip works.
# shellcheck source=migrate/lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"
parse_common "$@"
BUNDLE="$HERE" REPO="" REMOTE="" CODE_REPO="${CORKERN_CODE_REPO:-git@github.com:scpg/corkern.git}"
CODE_REF="${CORKERN_CODE_REF:-main}"
set -- "${ARGS[@]+"${ARGS[@]}"}"
while [ $# -gt 0 ]; do case "$1" in
    --repo) REPO="$2"; shift ;; --remote) REMOTE="$2"; shift ;; --bundle) BUNDLE="$2"; shift ;;
    --code-repo) CODE_REPO="$2"; shift ;; *) die "unknown option: $1" ;; esac; shift; done
STEPS=(install confirm-migration shell-profile secret-scan publish keys record-origin trust init-device iwe verify)
if [ "$SHOW_STATUS" = 1 ]; then show_status "$SCRIPT_NAME" "${STEPS[@]}"; exit 0; fi
KR="$(kr)"
if [ -n "$REPO" ]; then TARGET="git@github.com:$REPO.git"; else TARGET="$REMOTE"; fi

title "corkern migration — 2/3: set up this machine ($(hostname))"
say "  log: $LOG"
title "Preconditions"
need "export folder: $BUNDLE" "run 1-export-v1.sh on the v1 machine and copy its folder here" \
     test -f "$BUNDLE/v1-export.tar" -a -f "$BUNDLE/manifest.txt"
need "export intact (sha256)" "copy the folder again — it was damaged on the way" \
     bash -c "cd '$BUNDLE' && sha256sum -c v1-export.tar.sha256"
# (its own bash: a grep -q under pipefail would hide a match)
need "export has no conflict markers" "the export was made from a half-merged v1 — fix v1 and export again: bash migrate/1-export-v1.sh --restart" \
     bash -c "! tar -xOf '$BUNDLE/v1-export.tar' 2>/dev/null | grep -aqE '^(<<<<<<< |>>>>>>> )'"
need "where to publish: --repo <owner>/<name> or --remote <url>" "add one of the two options" test -n "$TARGET"
need "git knows who commits (user.name / user.email)" \
     "git config --global user.name 'Your Name'; git config --global user.email you@example.com" \
     bash -c "git config user.name && git config user.email"
need "no corkern v1 on this machine" "this is a v1 machine — use 3-switch-v1-machine.sh here" bash -c "$(declare -f v1_repo v1_command v1_installed); ! v1_installed"
if done_step install; then ok "knowledge repo already created by an earlier run ($KR)"
elif [ -f "$KR/.git/corkern/migrate-pending" ]; then
    ok "unfinished migration at $KR (nothing committed: the secret scan could not run) — this run finishes it"
else need "no knowledge repo yet at $KR" "move it away, or set CORKERN_MEMORY_DIR to another place" bash -c "! [ -e '$KR' ]"; fi
for t in git uv ssh-keygen flock timeout; do need "$t installed" "install $t (see README)" command -v "$t"; done
need "python3 >= 3.10" "install python3 3.10 or newer" py_ok
need "corkern code reachable: $CODE_REPO" "check GitHub access (ssh -T git@github.com) or pass --code-repo" \
     timeout 20 git ls-remote --quiet "$CODE_REPO" "$CODE_REF"
if [ -n "$REPO" ]; then
    need "gh (GitHub CLI) logged in" "gh auth login" gh auth status
    if ! done_step publish; then
        need "GitHub repo $REPO does not exist yet" "pick another name, or delete the empty repo" bash -c "! gh repo view '$REPO' >/dev/null 2>&1"
    fi
else
    need "remote reachable: $REMOTE" "create the empty repo first / check access" timeout 20 git ls-remote "$REMOTE"
    if ! done_step publish; then
        need "remote is empty: $REMOTE" "it already holds commits — use a new, empty repo (corkern never overwrites one)" \
             bash -c "[ -z \"\$(timeout 20 git ls-remote '$REMOTE' 2>/dev/null)\" ]"
    fi
fi
warn_if_not "Claude Code installed" "install it before using corkern (hooks are wired anyway)" command -v claude
warn_if_not "IWE installed (optional)" "the IWE step is skipped; add later: corkern iwe --setup --install" \
     bash -c "command -v iwec || test -x ~/.cargo/bin/iwec"
end_preflight

install() {
    local src="$MIG_DIR/code"
    rm -rf "$src"
    run git clone -q --branch "$CODE_REF" -- "$CODE_REPO" "$src" || return 1
    CORKERN_CODE_REPO="$CODE_REPO" CORKERN_CODE_REF="$CODE_REF" \
        run bash "$src/install.sh" --migrate "$BUNDLE/v1-export.tar" --memory-dir "$KR" || return 1
    v2_cli && test -f "$KR/corkern.conf"
}

confirm_migration() {
    grep -q '^format=corkern-export' "$BUNDLE/manifest.txt" || { ok "git-archive bundle — nothing more to compare"; return 0; }
    local out; out=$(c2 import "$BUNDLE/v1-export.tar" --check 2>&1); printf '%s\n' "$out" >> "$LOG"
    printf '%s\n' "$out" | grep -E "identical|missing here|different here|not here" | sed 's/^/  /'
    if printf '%s\n' "$out" | grep -qE "missing here|different here|not here"; then
        err "the knowledge repo does not match the export"; return 1; fi
    ok "every file of the export is in the knowledge repo, identical"
}

shell_profile() {
    # only PATH, written with $HOME: the line is the same on every machine (shared dotfiles);
    # the knowledge repo is recorded in this machine's machine.conf by corkern setup
    local rc="$HOME/.bashrc"
    [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-}")" = zsh ] && rc="$HOME/.zshrc"
    case ":$PATH:" in *":$HOME/.local/bin:"*) ok "$HOME/.local/bin is already on PATH — $rc stays as it is"; return 0 ;; esac
    if grep -q "# corkern v2" "$rc" 2>/dev/null; then ok "$rc already has the corkern line"; return 0; fi
    say "  Line for $rc (so the terminal finds corkern):"
    say "      export PATH=\"\$HOME/.local/bin:\$PATH\""
    if ask "Add it to $rc?"; then
        # shellcheck disable=SC2016  # $HOME/$PATH are meant literally in the profile
        printf '\n# corkern v2\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
        ok "added — new terminals pick it up"
    else warn "not added — call corkern as ~/.local/bin/corkern, or add the line yourself"; fi
}

secret_scan() {
    if run c2 secrets --scan --history; then ok "no secrets in the migrated memories"; return 0; fi
    warn "possible secrets above are in the v1 data (and in v1's own history)."
    ask "Publish anyway? (No = stop here; edit the files in $KR, commit, and run again)"
}

record_origin() {
    printf 'v1_export_commit=%s\nsource_host=%s\nmigrated=%s\nmigrated_on=%s\n' \
        "$(grep '^v1_export_commit=' "$BUNDLE/manifest.txt" | cut -d= -f2)" \
        "$(grep '^source_host=' "$BUNDLE/manifest.txt" | cut -d= -f2)" "$(_ts)" "$(hostname)" > "$KR/.corkern-migration"
    run git -C "$KR" add .corkern-migration && run git -C "$KR" commit -q -m "corkern: record the v1 export point" || return 1
    ok "export point recorded in the knowledge repo (used when other v1 machines switch)"
}

publish() {
    if [ -n "$REPO" ]; then
        run gh repo create "$REPO" --private --source "$KR" --remote origin --push || return 1
        [ "$(gh repo view "$REPO" --json visibility -q .visibility)" = PRIVATE ] || { err "$REPO is not private!"; return 1; }
        ok "published to PRIVATE GitHub repo $REPO"
    else
        git -C "$KR" remote get-url origin >/dev/null 2>&1 || run git -C "$KR" remote add origin "$REMOTE" || return 1
        run git -C "$KR" push -q -u origin HEAD || return 1
        ok "published to $REMOTE"
    fi
}

keys() {
    say "  This machine gets its own signing key (all memory commits are signed with it)"
    [ -n "$REPO" ] && say "  and a deploy key (push access to $REPO only), so syncing never waits for 1Password."
    ask "Create them?" || { warn "skipped — run later: corkern signing"; return 0; }
    if [ -n "$REPO" ]; then run c2 signing --yes --register-github --deploy-key || return 1
    else run c2 signing --yes < /dev/null || return 1; fi
    run c2 signing --check || warn "see the check above (warnings are fine)"
    return 0
}

trust() {
    git -C "$KR" config --local --get user.signingKey >/dev/null || { warn "no signing key — trust skipped"; return 0; }
    run c2 trust --enable --yes || return 1
    ok "only memories signed by trusted devices are accepted (add your other machines when they switch)"
}

init_device() {
    run c2 init-device || return 1
    ask "Apply this (project memory, hooks, global CLAUDE.md, slash commands)?" \
        || { warn "skipped — run later: corkern init-device --apply"; return 0; }
    run c2 init-device --apply --yes
}

iwe_step() {
    command -v iwec >/dev/null || [ -x "$HOME/.cargo/bin/iwec" ] || { warn "IWE not installed — skipped"; return 0; }
    run c2 iwe --setup && run c2 iwe --install --yes
}

verify() {
    local out head remote
    out=$(c2 status -v 2>&1); printf '%s\n' "$out" >> "$LOG"
    printf '%s\n' "$out" | grep -E "✗|Hooks wired|problem\(s\)|All good" | sed 's/^/    /'
    printf '%s\n' "$out" | grep -q "Hooks wired in Claude Code" || warn "hooks not wired (init-device skipped?)"
    local mark todo="$KR/memory/global/todo.md"
    mark="corkern migration check $(date +%s)"
    run c2 remind "$mark — remove me" global >/dev/null || return 1
    hook corkern-push >> "$LOG" 2>&1
    head=$(git -C "$KR" rev-parse HEAD)
    remote=$(git -C "$KR" ls-remote origin HEAD | cut -f1)
    [ "$head" = "$remote" ] || { err "sync test: the remote did not receive the test commit"; return 1; }
    # the test reminder goes again (not just ticked off): todo.md is as before the test
    grep -vF -- "$mark" "$todo" > "$todo.tmp" && mv "$todo.tmp" "$todo"
    hook corkern-push >> "$LOG" 2>&1
    ok "sync round trip works (commit → remote)"
}

title "Steps"
step install       "Install corkern v2 and build the knowledge repo from the export" install
step confirm-migration "Confirm: everything in the export arrived"                 confirm_migration
step shell-profile "Shell profile (PATH)"                                          shell_profile
step secret-scan   "Check the migrated memories for secrets"                        secret_scan
step publish       "Publish the knowledge repo (private)"                           publish
step keys          "This machine's signing key (+ push key)"                        keys
step record-origin "Record where the data came from"                                record_origin
step trust         "Pull verification (trust)"                                      trust
step init-device   "Wire Claude Code"                                               init_device
step iwe           "IWE knowledge graph (optional)"                                 iwe_step
step verify        "Check everything and test a sync round trip"                    verify

title "Done — this machine runs corkern v2"
say "  Try it: open Claude Code in one of your projects (trust the folder when asked),"
say "  check /memory and /hooks, and look for the corkern box at session start."
say "  Health any time: corkern status -v        Progress: bash $HERE/status.sh"
say "  When v2 has proven itself here, switch your v1 machines with 3-switch-v1-machine.sh."
say "  Log: $LOG"
