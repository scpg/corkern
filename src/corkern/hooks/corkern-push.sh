#!/usr/bin/env bash
# Stop / SessionEnd — runs after each Claude reply, while Claude is idle: the safe moment
# to touch files on disk. Commit leftovers, integrate other devices' memories (at most
# every PULL_EVERY seconds, or whenever we have something to push), then push.
# shellcheck source=hooks/lib.sh
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib.sh"

PULL_EVERY=600

ck_init push || exit 0
ck_lock 30 || { ck_note push "lock busy for 30s"; exit 0; }

ck_commit_all "auto: memory" || true
# Runs after EVERY Claude reply: with nothing to push and no pull due, don't touch the
# network at all (each remote check costs ~1 s of SSH handshake on GitHub).
if ! ck_ahead && ! ck_due pull "$PULL_EVERY"; then exit 0; fi
ck_preflight push || exit 0
if ck_ahead || ck_due pull "$PULL_EVERY"; then ck_pull pull || true; fi
if ck_ahead; then ck_push push || true; fi
exit 0
