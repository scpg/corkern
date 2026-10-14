#!/usr/bin/env bash
# corkern v2 — Claude Code hook entry point.
#
#   bash <code-dir>/hooks/corkern.sh corkern-session-start   # SessionStart: pull, then context
#   bash <code-dir>/hooks/corkern.sh corkern-commit-memory   # PostToolUse (Write|Edit)
#   bash <code-dir>/hooks/corkern.sh corkern-push            # Stop / SessionEnd
#   (corkern-pull and corkern-session-notify can also be called on their own)
#
# The knowledge repo comes from CORKERN_MEMORY_DIR. Unknown hook names and every
# failure exit 0 — corkern must never block or break a Claude session. Only the
# session context (notify) writes to stdout, because Claude Code injects it.
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)" || exit 0
case "${1:-}" in
    corkern-session-start|corkern-pull|corkern-push|corkern-commit-memory|corkern-session-notify) ;;
    *) exit 0 ;;
esac

payload=""
[ -t 0 ] || payload=$(timeout 2 cat 2>/dev/null)

run() {  # run HOOK — silent hooks get no stdout; notify passes its stdout through
    if [ "$1" = corkern-session-notify ]; then
        printf '%s' "$payload" | bash "$here/$1.sh" 2>/dev/null
    else
        bash "$here/$1.sh" </dev/null >/dev/null 2>&1
    fi
}

trace() {  # opt-in debug trace (CORKERN_HOOK_LOG=<file>)
    {
        echo "$(date '+%F %T') $1 start pid=$$ cwd=$PWD memdir=${CORKERN_MEMORY_DIR:-<unset>} stdin=$(printf '%s' "${payload:-<none>}" | tr -d '\n' | cut -c1-200)"
        [ -n "${CORKERN_MEMORY_DIR:-}" ] && GIT_OPTIONAL_LOCKS=0 git -C "$CORKERN_MEMORY_DIR" \
            status --porcelain --untracked-files=all 2>&1 | sed 's/^/  status: /'
        if [ "$1" = corkern-session-notify ]; then echo "  (stdout passed to Claude)"
        else bash -x "$here/$1.sh" </dev/null 2>&1 >/dev/null | tail -40; fi
        echo "$(date '+%F %T') $1 end"
    } >> "$CORKERN_HOOK_LOG" 2>&1
}

steps=("$1")
[ "$1" = corkern-session-start ] && steps=(corkern-pull corkern-session-notify)
for step in "${steps[@]}"; do
    [ -n "${CORKERN_HOOK_LOG:-}" ] && trace "$step"
    if [ -n "${CORKERN_HOOK_LOG:-}" ] && [ "$step" != corkern-session-notify ]; then continue; fi
    run "$step"
done
exit 0
