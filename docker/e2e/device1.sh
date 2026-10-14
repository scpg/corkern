#!/usr/bin/env bash
# Device 1 (alice): fresh install, v1 isolation, real-format migration, day-to-day use,
# publish the knowledge repo for device 2. v1 fingerprint must be identical at the end.
set -uo pipefail
# shellcheck source=docker/e2e/lib.sh
. "$(dirname "$0")/lib.sh"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export CORKERN_CODE_REPO=/opt/code CORKERN_CODE_REF=main
INSTALL=/opt/code/install.sh
V1="$HOME/dev/corkern"
unset CORKERN_MEMORY_DIR

fingerprint > /tmp/v1-before."$USER"
V1CMD="$HOME/.local/bin/corkern"            # v1's command: a link into its repo
V1_CMD_BEFORE=$("$V1CMD" status)
RC_BEFORE=$(cd "$HOME" && sha256sum .bashrc .profile .bash_profile 2>/dev/null)

section "install.sh refuses to replace v1's corkern"
refuse "install while v1 is installed"     "corkern v1 is installed here" bash "$INSTALL" --new
assert "…v1's command untouched"           test "$(readlink "$V1CMD")" = "$V1/corkern"
assert "…nothing installed"                test ! -e "$HOME/.corkern"

# The rest checks that v2 never touches v1 data even when both exist (as during a switch):
# test-only, v2 goes into its own bin folder so v1's command stays where it is.
export UV_TOOL_BIN_DIR="$HOME/.local/v2bin" PATH="$HOME/.local/v2bin:$PATH"
section "install.sh --new (fresh device, v1 present, v2 in its own bin folder)"
expect "install --new succeeds"            "Created knowledge repo" bash "$INSTALL" --new
assert "corkern installed"                 test -x "$HOME/.local/v2bin/corkern"
expect "v1 'corkern' still the v1 command"  "corkern v1 status" "$V1CMD" status
assert "v1 'corkern' output unchanged"      test "$("$V1CMD" status)" = "$V1_CMD_BEFORE"
expect "corkern --version"                 "corkern 2\." corkern --version
expect "install names this machine's repo"  "corkern on this machine uses" bash "$INSTALL"
assert "shell rc files unchanged"           test "$(cd "$HOME" && sha256sum .bashrc .profile .bash_profile 2>/dev/null)" = "$RC_BEFORE"

section "v1 refusals — every command × every v1 path"
SNEAKY="$HOME/sneaky"; ln -s "$V1" "$SNEAKY"
cp -r "$V1" "$HOME/v1-copy-no-sentinel"; rm -f "$HOME/v1-copy-no-sentinel/.corkern-v1.sentinel"
for p in "$V1" "$V1/memory" "$V1/memory/corkern" "$V1/memory/corkern/sessions" \
         "$HOME/.claude/projects/-home-$USER-dev-projects-webshop/memory" "$HOME/.claude" \
         "$SNEAKY" "$SNEAKY/memory" "$HOME/v1-copy-no-sentinel/memory"; do
    short=${p#"$HOME"/}
    for cmd in "list" "status" "add probe /tmp" "remove corkern"; do
        # shellcheck disable=SC2086
        refuse "$cmd @ ~/$short" "v1-protected" env CORKERN_MEMORY_DIR="$p" corkern $cmd
    done
    refuse "init @ ~/$short/new"          "v1-protected" corkern init "$p/new"
    refuse "migrate dest @ ~/$short/new"  "v1-protected" corkern migrate /tmp/none.tar "$p/new"
done
refuse "HOME overridden still protects real v1" "v1-protected" env HOME=/tmp CORKERN_MEMORY_DIR="$V1" corkern list
refuse "\$CORKERN location protected"           "v1-protected" env CORKERN="$HOME/elsewhere" CORKERN_MEMORY_DIR="$HOME/elsewhere/x" corkern list

section "install.sh refusals"
refuse "--memory-dir inside v1"     "v1-protected" bash "$INSTALL" --new --memory-dir "$V1/kr"
refuse "--memory-dir inside .claude" "v1-protected" bash "$INSTALL" --new --memory-dir "$HOME/.claude/kr"
refuse "--memory-dir via symlink"    "v1-protected" bash "$INSTALL" --new --memory-dir "$SNEAKY/kr"
refuse "CORKERN_CODE_DIR inside v1"  "v1-protected" env CORKERN_CODE_DIR="$V1/app" bash "$INSTALL"
refuse "conflicting modes"           "choose only one" bash "$INSTALL" --new --clone x
refuse "unknown flag"                "unknown option" bash "$INSTALL" --frobnicate

section "migrate from the real export command"
check  "export v1 (git archive, read-only)" sh -c "git -C '$V1' archive HEAD memory .devices global projects.conf > '$HOME/v1-export.tar'"
expect "dry-run counts memory files"  "memory files : 8" corkern migrate "$HOME/v1-export.tar" "$HOME/kr" --dry-run
assert "dry-run wrote nothing"        test ! -e "$HOME/kr"
expect "migrate"                       "committed v1 data only" corkern migrate "$HOME/v1-export.tar" "$HOME/kr"
export CORKERN_MEMORY_DIR="$HOME/kr"
mismatch=0
while IFS= read -r f; do
    case "$f" in */) continue ;; esac
    dst="$f"; [ "$f" = projects.conf ] && dst=corkern.conf
    git -C "$V1" show "HEAD:$f" | cmp -s - "$HOME/kr/$dst" || { echo "     │ differs: $f"; mismatch=1; }
done < <(tar -tf "$HOME/v1-export.tar")
assert "every exported file byte-identical (incl. binary)" test "$mismatch" = 0
assert "v1 code not migrated"          test ! -e "$HOME/kr/hooks" -a ! -e "$HOME/kr/corkern"
assert "uncommitted v1 memory absent"  test ! -e "$HOME/kr/memory/webshop/uncommitted.md"
assert "uncommitted v1 project absent" bash -c "! grep -q newproj '$HOME/kr/corkern.conf'"
expect "relative path resolves under HOME"  "webshop .*$HOME/dev/projects/webshop .*not wired" corkern list
expect "empty path = HOME (v1 owns ~/.claude here)" "home .*$HOME .*v1 location" corkern list
expect "absolute path kept"                 "shared .*/opt/shared/proj .*not wired" corkern list
expect "duplicate slug: first line wins"    "dup .*dev/dup-first .*not wired" corkern list
expect "missing project shown as missing"   "missing .*no path" corkern list
assert "Windows-only line skipped"          bash -c "! corkern list | grep -q winonly"
assert "clean git tree after migrate"       test -z "$(git -C "$HOME/kr" status --porcelain)"

section "day-to-day on the migrated repo"
mkdir -p "$HOME/dev/projects/newone"
check  "add project under HOME"               corkern add newone "$HOME/dev/projects/newone"
assert "stored HOME-relative (portable)"      grep -qx 'newone:dev/projects/newone' "$HOME/kr/corkern.conf"
refuse "slug with ':' rejected"   "Invalid slug" corkern add 'a:b' /tmp
refuse "slug with '/' rejected"   "Invalid slug" corkern add 'a/b' /tmp
refuse "dot slug rejected"        "Invalid slug" corkern add -- '..' /tmp
refuse "duplicate slug rejected"  "already exists" corkern add webshop /tmp
check  "remove a project"         corkern remove missing
assert "removed from registry"    bash -c "! grep -q '^missing:' '$HOME/kr/corkern.conf'"
refuse "init on a file: clear error" "not a directory" corkern init "$HOME/v1-export.tar"
refuse "init under a file: clear error" "not a directory" corkern init "$HOME/v1-export.tar/kr"
refuse "same path under a 2nd slug"  "already tracked under slug 'newone'" corkern add newone2 "$HOME/dev/projects/newone"
refuse "unicode line break in path"  "line break" corkern add uni "$(printf '/tmp/a evil:/x')"
echo 'ghost:~nosuchuser/x' >> "$HOME/kr/corkern.conf"
expect "'~unknownuser' path: no crash, v1 semantics" "ghost .*$HOME/~nosuchuser/x" corkern list
expect "remove reports lines removed" "Removed ghost (1 line, all devices)" corkern remove ghost
echo 'onlylocal:/x' > "$HOME/kr/corkern.conf.local"
refuse "remove .local-only slug explains" "corkern.conf.local" corkern remove onlylocal
rm "$HOME/kr/corkern.conf.local"
echo '../bad:dev/x' >> "$HOME/kr/corkern.conf"
expect "invalid slug in file: warned and skipped" "skipping invalid slug" corkern list
sed -i '/^\.\.\/bad:/d' "$HOME/kr/corkern.conf"
refuse "a second knowledge repo on this machine is refused" "already uses" bash "$INSTALL" --memory-dir "$HOME/kr"
rm "$HOME/.config/corkern/machine.conf"   # switching repos is a deliberate step
expect "install rerun on migrated repo" "Using existing knowledge repo" bash "$INSTALL" --memory-dir "$HOME/kr"

section "graphify (real graphify, code-only — no API key)"
G="$HOME/dev/projects/webshop"
printf 'def parse(x):\n    return helper(x)\n\ndef helper(x):\n    return x * 2\n' > "$G/app.py"
expect "graphify builds a code-only graph"   "code-only graph" corkern graphify webshop
assert "graph.json produced"                  test -s "$G/graphify-out/graph.json"
expect "graph contains the code's functions"  "helper" cat "$G/graphify-out/graph.json"
refuse "graphify on the v1 repo refused"      "v1-protected" corkern graphify corkern
refuse "graphify --install on home refused"   "Refusing --install" corkern graphify home --install
assert "nothing written into v1 by graphify"  test ! -e "$V1/graphify-out"

section "crg (real code-review-graph 2.3.9)"
expect "crg builds the graph"                 "Graph:" corkern crg webshop
assert "graph database created in the project" test -e "$G/.code-review-graph"
expect "crg --install registers it"           "installed" corkern crg webshop --install
refuse "crg on the v1 repo refused"           "v1-protected" corkern crg corkern
refuse "crg --install on home refused"        "Refusing --install" corkern crg home --install
assert "nothing written into v1 by crg"        test ! -e "$V1/.code-review-graph"

section "publish knowledge repo for device 2"
check "commit changes"  git -C "$HOME/kr" commit -qam "device1 changes"
check "push to shared bare repo" sh -c "git clone -q --bare '$HOME/kr' /srv/shared/kr.git && chmod -R a+rX /srv/shared/kr.git"

section "v1 untouched"
fingerprint > /tmp/v1-after."$USER"
if diff /tmp/v1-before."$USER" /tmp/v1-after."$USER" > "$OUT"; then ok "v1 repo, ~/.claude and v1 command byte-identical"
else bad "v1 CHANGED"; show; fi
assert "v1 still has its uncommitted work" grep -q newproj "$V1/projects.conf"

finish
