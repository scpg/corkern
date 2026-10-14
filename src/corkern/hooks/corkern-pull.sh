#!/usr/bin/env bash
# SessionStart: flush commits left unpushed by an earlier session, then pull.
# shellcheck source=hooks/lib.sh
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib.sh"

ck_init pull || exit 0
ck_preflight pull || exit 0
ck_lock 30 || { ck_note pull "lock busy for 30s"; exit 0; }

if ck_ahead; then ck_push push || true; fi
ck_pull pull || true
exit 0
