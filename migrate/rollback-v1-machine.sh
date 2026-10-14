#!/usr/bin/env bash
# Undo 3-switch-v1-machine.sh on this machine: v1 back exactly as it was, v2 unwired from Claude
# Code. Close all Claude Code sessions first.
#
#   bash migrate/rollback-v1-machine.sh [--yes] [--dry-run] [--status]
#
# Uses the backup made by step 3: settings.json and every link v1 had are restored, v1's IWE
# server is registered again, the repo moves back to ~/dev/corkern. v2's settings files are
# moved aside (not deleted). Memories written with v2 meanwhile stay in the knowledge repo
# (they are listed at the end) — nothing is lost.
# shellcheck source=migrate/lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"
parse_common "$@"
[ "${#ARGS[@]}" -eq 0 ] || die "unknown option: ${ARGS[0]}"
STEPS=(unwire-projects restore-settings move-aside-v2 restore-v1 restore-iwe verify)
if [ "$SHOW_STATUS" = 1 ]; then show_status "$SCRIPT_NAME" "${STEPS[@]}"; exit 0; fi
V1="$(v1_repo)"
RETIRED="$V1-v1-retired"
KR="$(kr)"
BACKUP="$MIG_DIR/backup"
ASIDE="$MIG_DIR/v2-aside-$(date +%Y%m%d-%H%M%S)"
SWITCH_STATE="$MIG_DIR/3-switch-v1-machine.state"

title "corkern migration — rollback this machine to v1 ($(hostname))"
say "  log: $LOG"
title "Preconditions"
need "the switch ran here (its backup exists)" "nothing to roll back — 3-switch-v1-machine.sh did not run on this machine" \
     test -f "$BACKUP/links.tsv"
if done_step restore-v1; then ok "v1 already restored by an earlier run"
else
    need "retired v1 repo present: $RETIRED" "it was moved or deleted — restore it from your v1 remote (git clone)" test -d "$RETIRED"
    need "$V1 is free" "something is at $V1 already — move it away" bash -c "! [ -e '$V1' ]"
fi
need "no Claude Code session running" "close all Claude Code sessions" bash -c "! pgrep -u $(id -u) -x claude"
need "python3 available" "install python3" command -v python3
end_preflight
ask "Roll this machine back to v1?" || die "nothing changed."

unwire_projects() {   # remove autoMemoryDirectory → knowledge repo from projects' local settings
    local n=0 f
    while IFS= read -r -d '' f; do
        grep -q "$KR" "$f" 2>/dev/null || continue
        python3 - "$f" "$KR" <<'PY' || return 1
import json, sys
path, kr = sys.argv[1], sys.argv[2]
data = json.load(open(path))
if str(data.get("autoMemoryDirectory", "")).startswith(kr):
    del data["autoMemoryDirectory"]
    json.dump(data, open(path, "w"), indent=2)
PY
        logf "unwired: $f"; n=$((n + 1))
    done < <(find "$HOME" -maxdepth 7 -path '*/.claude/settings.local.json' -not -path "$HOME/.claude/*" -print0 2>/dev/null)
    ok "$n project(s): autoMemoryDirectory removed (other settings kept)"
}

restore_settings() {
    mkdir -p "$ASIDE"
    [ -f "$HOME/.claude/settings.json" ] && cp -p "$HOME/.claude/settings.json" "$ASIDE/settings.json"
    if [ -f "$BACKUP/settings.json" ]; then cp -p "$BACKUP/settings.json" "$HOME/.claude/settings.json"; ok "settings.json restored (v2's kept in $ASIDE)"
    else warn "no settings.json in the backup — left as is"; fi
}

move_aside_v2() {
    mkdir -p "$ASIDE/commands"
    if [ -f "$HOME/.claude/CLAUDE.md" ] && [ ! -L "$HOME/.claude/CLAUDE.md" ]; then mv "$HOME/.claude/CLAUDE.md" "$ASIDE/CLAUDE.md"; fi
    local f
    for f in "$HOME"/.claude/commands/corkern-*.md; do [ -f "$f" ] && [ ! -L "$f" ] && mv "$f" "$ASIDE/commands/"; done
    if command -v claude >/dev/null && claude mcp get iwe 2>/dev/null | grep -q iwe-serve; then
        run claude mcp remove iwe --scope user || return 1
    fi
    # v2's `corkern` command takes the name v1's link needs; the knowledge repo stays untouched
    if v2_cli; then run uv tool uninstall corkern || { command -v pipx >/dev/null && run pipx uninstall corkern; } || return 1; fi
    ok "v2's CLAUDE.md, slash commands, IWE server and command moved aside ($ASIDE)"
}

restore_v1() {
    run mv "$RETIRED" "$V1" || return 1
    local link target
    while IFS=$'\t' read -r link target; do
        [ -n "$link" ] || continue
        mkdir -p "$(dirname "$link")"
        if [ -e "$link" ] && [ ! -L "$link" ]; then mv "$link" "$ASIDE/$(echo "$link" | tr / _)"; fi
        ln -sfn "$target" "$link"
    done < "$BACKUP/links.tsv"
    ok "v1 repo back at $V1, $(wc -l < "$BACKUP/links.tsv") link(s) restored"
}

restore_iwe() {
    [ -f "$BACKUP/iwe-mcp.txt" ] || { ok "v1 had no IWE server — nothing to do"; return 0; }
    local cmd; cmd=$(sed -n 's/^ *Command: *//p' "$BACKUP/iwe-mcp.txt" | head -1)
    [ -n "$cmd" ] || { warn "could not read v1's IWE command from the backup — re-add it by hand"; return 0; }
    run claude mcp add iwe --scope user -- "$cmd" || return 1
    ok "v1's IWE server registered again ($cmd)"
}

verify() {
    if ! test -x "$V1/corkern" || ! run "$V1/corkern" status >/dev/null; then err "v1's corkern status failed"; return 1; fi
    if grep -q 'share/corkern/hooks/corkern.sh' "$HOME/.claude/settings.json" 2>/dev/null; then err "v2 hooks still in settings.json"; return 1; fi
    ok "v1 is back"
    if [ -d "$KR/.git" ]; then
        local since; since=$(grep '^step:install=' "$SWITCH_STATE" 2>/dev/null | cut -d= -f2)
        say "  Memories written with v2 stay in $KR$([ -n "$since" ] && echo " (since $since)"):"
        git -C "$KR" log --since="${since:-1 day ago}" --format='    %h %ad %s' --date=short | head -20 | tee -a "$LOG"
    fi
}

title "Steps"
step unwire-projects  "Unwire projects from the knowledge repo"   unwire_projects
step restore-settings "Restore v1's settings.json"                restore_settings
step move-aside-v2    "Move v2's Claude Code files aside"         move_aside_v2
step restore-v1       "Restore v1 (repo + links)"                 restore_v1
step restore-iwe      "Restore v1's IWE server"                   restore_iwe
step verify           "Check v1 works"                            verify

title "Done — this machine runs corkern v1 again"
say "  To try the switch again later: remove $MIG_DIR/3-switch-v1-machine.state and $MIG_DIR/$SCRIPT_NAME.state,"
say "  move $KR away, then run 3-switch-v1-machine.sh."
say "  Log: $LOG"
