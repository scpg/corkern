#!/usr/bin/env bash
# PostToolUse (Write|Edit): commit memory writes as they happen; fast no-op otherwise.
# Pushes in the background (global/ memories at once, project memories at most every 3 h).
#
# The background push NEVER integrates remote changes: rebasing rewrites files on disk,
# and Claude may be writing the next memory at that very moment (the 5-device stress test
# lost writes that way). If the remote moved on, the push simply waits for the Stop hook,
# which integrates while Claude is idle.
# shellcheck source=hooks/lib.sh
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib.sh"

PUSH_EVERY=10800

ck_init commit-memory || exit 0

# shellcheck disable=SC2329  # invoked indirectly via ck_bg
_bg_push() {
    ck_lock 15 || return 0
    ck_preflight push || return 0
    ck_ahead || return 0
    if ck_git push -q >/dev/null 2>&1; then ck_stamp push; fi   # rejected → Stop hook handles it
}

COMMITTED=""
if ck_memory_dirty && ck_lock 10; then
    if ck_memory_dirty; then ck_commit_memory "memory"; fi   # re-check under the lock
    exec 9>&-                      # release the lock before any network I/O
fi
[ -n "$COMMITTED" ] || exit 0

case " $COMMITTED" in
    *" global/"*) ck_bg _bg_push ;;
    *) if ck_due push "$PUSH_EVERY"; then ck_bg _bg_push; fi ;;
esac
exit 0
