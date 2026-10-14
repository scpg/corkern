# shellcheck shell=bash
# Assertion helpers for the e2e scenarios. Every check prints one line; failures show output.
PASS=0 FAIL=0
OUT=$(mktemp)

ok()  { echo "  ✅ $1"; PASS=$((PASS + 1)); }
bad() { echo "  ❌ $1"; FAIL=$((FAIL + 1)); }
show() { sed 's/^/     │ /' "$OUT" | head -20; }
section() { echo; echo "── $* ──"; }

# Run a command into $OUT; sets RC. A Python traceback is never acceptable output —
# users must get a clear message, so any check that produced one fails.
run_cmd() { "$@" >"$OUT" 2>&1; RC=$?; }
traceback() { grep -q '^Traceback (most recent call last)' "$OUT"; }

# check DESC CMD... — command must succeed
check() {
    local d="$1"; shift
    run_cmd "$@"
    if traceback; then bad "$d (Python traceback)"; show
    elif [ "$RC" -eq 0 ]; then ok "$d"
    else bad "$d (exit $RC)"; show; fi
}

# refuse DESC PATTERN CMD... — command must fail AND print PATTERN
refuse() {
    local d="$1" pat="$2"; shift 2
    run_cmd "$@"
    if traceback; then bad "$d (Python traceback)"; show
    elif [ "$RC" -eq 0 ]; then bad "$d (unexpectedly succeeded)"; show
    elif grep -q -- "$pat" "$OUT"; then ok "$d"
    else bad "$d (exit $RC without '$pat')"; show; fi
}

# expect DESC PATTERN CMD... — command must succeed AND print PATTERN
expect() {
    local d="$1" pat="$2"; shift 2
    run_cmd "$@"
    if traceback; then bad "$d (Python traceback)"; show
    elif [ "$RC" -ne 0 ]; then bad "$d (exit $RC)"; show
    elif grep -q -- "$pat" "$OUT"; then ok "$d"
    else bad "$d (no '$pat' in output)"; show; fi
}

# assert DESC TEST-EXPR... — plain condition
assert() {
    local d="$1"; shift
    if "$@"; then ok "$d"; else bad "$d"; fi
}

# Fingerprint everything v1 owns for the current user: the v1 repo (incl. .git),
# ~/.claude, and the v1 `corkern` command — paths, link targets, modes, contents.
fingerprint() {
    (cd "$HOME" || exit 1
     local -a roots=(dev/corkern .claude .local/bin/corkern)
     find "${roots[@]}" -printf '%p|%y|%l|%m\n' 2>/dev/null | LC_ALL=C sort
     find "${roots[@]}" -type f -print0 2>/dev/null | LC_ALL=C sort -z | xargs -0 sha256sum)
}

finish() {
    echo
    echo "══ $(whoami): $PASS passed, $FAIL failed ══"
    rm -f "$OUT"
    [ "$FAIL" -eq 0 ]
}
