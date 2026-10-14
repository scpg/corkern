#!/usr/bin/env bash
# SessionStart context: corkern instructions, open reminders, actionable memory files.
# Unlike the other hooks its stdout IS the point — Claude Code adds it to the session
# context. Prints only what `corkern session-context` produced; any problem → silence.
# shellcheck source=hooks/lib.sh
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib.sh"

ck_init session-notify || exit 0
bin=$(ck_bin) || { ck_note session-notify "corkern not found"; exit 0; }
if out=$("$bin" session-context 2>/dev/null); then
    printf '%s\n' "$out"
else
    ck_note session-notify "corkern session-context failed"
fi
exit 0
