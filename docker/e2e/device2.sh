#!/usr/bin/env bash
# Device 2 (bob, different HOME, own v1): install by cloning device 1's knowledge repo.
# HOME-relative paths written on device 1 must resolve under /home/bob here.
set -uo pipefail
# shellcheck source=docker/e2e/lib.sh
. "$(dirname "$0")/lib.sh"
# bob has v1 too: v2 goes into its own bin folder (test-only), v1's `corkern` link stays
export UV_TOOL_BIN_DIR="$HOME/.local/v2bin"
export PATH="$HOME/.local/v2bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export CORKERN_CODE_REPO=/opt/code CORKERN_CODE_REF=main
INSTALL=/opt/code/install.sh
unset CORKERN_MEMORY_DIR

fingerprint > /tmp/v1-before."$USER"

section "install.sh --clone (second device)"
expect "install --clone succeeds" "Cloning knowledge repo" bash "$INSTALL" --clone /srv/shared/kr.git
export CORKERN_MEMORY_DIR="$HOME/.corkern"
expect "device-1 project resolves under bob's HOME"  "webshop .*/home/bob/dev/projects/webshop .*not wired" corkern list
expect "project added on device 1 is portable"       "newone .*/home/bob/dev/projects/newone" corkern list
expect "project absent on this device shows ✗"       "toolbox .*no path" corkern list
expect "memory from device 1 present"                 "v1 state for alice" cat "$HOME/.corkern/memory/corkern/project_state.md"
assert "device-1 removal synced"                      bash -c "! grep -q '^missing:' '$HOME/.corkern/corkern.conf'"

section "per-device override"
mkdir -p /tmp/bob-webshop
echo "webshop:/tmp/bob-webshop" > "$HOME/.corkern/corkern.conf.local"
expect "corkern.conf.local overrides"   "webshop .*/tmp/bob-webshop .*not wired" corkern list
assert "override stays local (gitignored)" test -z "$(git -C "$HOME/.corkern" status --porcelain)"

section "bob's v1 untouched"
expect "v1 command still v1" "corkern v1" "$HOME/.local/bin/corkern" list
fingerprint > /tmp/v1-after."$USER"
if diff /tmp/v1-before."$USER" /tmp/v1-after."$USER" > "$OUT"; then ok "v1 repo, ~/.claude and v1 command byte-identical"
else bad "v1 CHANGED"; show; fi

finish
