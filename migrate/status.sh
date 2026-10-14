#!/usr/bin/env bash
# Where this machine stands in the v1 → v2 migration: which scripts ran, which steps are done,
# what is installed now, and what to run next.
#   bash migrate/status.sh
# shellcheck source=migrate/lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"
title "corkern migration status — $(hostname)"
show_status 1-export-v1          commit-v1 integrate record-commit export verify pack
show_status 2-setup-new-machine  install confirm-migration shell-profile secret-scan publish keys record-origin trust init-device iwe verify
show_status 3-switch-v1-machine  final-v1-sync final-export backup retire-v1 install keys carry-over confirm trust push init-device iwe verify
show_status rollback-v1-machine  unwire-projects restore-settings move-aside-v2 restore-v1 restore-iwe verify
title "This machine now"
if v1_installed; then echo "  corkern v1: installed ($(v1_repo))"; else echo "  corkern v1: not installed"; fi
[ -d "$(v1_repo)-v1-retired" ] && echo "  retired v1 kept at $(v1_repo)-v1-retired"
if v2_cli && [ -f "$(kr)/corkern.conf" ]; then
    echo "  corkern v2: installed, knowledge repo $(kr) → $(git -C "$(kr)" remote get-url origin 2>/dev/null || echo 'no remote')"
    if grep -q 'share/corkern/hooks/corkern.sh' "$HOME/.claude/settings.json" 2>/dev/null; then echo "  Claude Code hooks: v2"
    elif grep -q '\.claude/corkern\.sh' "$HOME/.claude/settings.json" 2>/dev/null; then echo "  Claude Code hooks: v1"
    else echo "  Claude Code hooks: none"; fi
else echo "  corkern v2: not installed"; fi
title "Next"
if grep -q '^step:verify=' "$MIG_DIR/3-switch-v1-machine.state" 2>/dev/null || grep -q '^step:verify=' "$MIG_DIR/2-setup-new-machine.state" 2>/dev/null; then
    echo "  This machine is migrated. Health: corkern status -v"
elif v1_installed; then
    echo "  v1 machine: 1-export-v1.sh (once, on one machine), later 3-switch-v1-machine.sh --repo <you>/<repo>"
else
    echo "  New machine: run 2-setup-new-machine.sh --repo <you>/<repo> from the folder 1-export-v1.sh made"
fi
echo "  Logs: $MIG_DIR/*.log"
