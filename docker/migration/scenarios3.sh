#!/usr/bin/env bash
# 3-switch-v1-machine.sh against the states a second v1 machine (like host-b) can be in when it is
# switched: each scenario is a fresh user with a REAL v1 (cloned from the v1 remote, wired by its
# own sync.sh), switched against its own copy of the published knowledge repo. Judged by invariants:
#
#   every run     no Python traceback; no conflict markers in the knowledge repo
#   success       v1 retired (kept), every FACT written on v1 after the export is in v2, the
#                 knowledge repo is pushed, files land at their exact names
#   refusal       says how to fix it; v1 is NOT retired: still in place, still wired (settings.json
#                 and links as before); every FACT still in v1
set -uo pipefail
# shellcheck source=docker/e2e/lib.sh
. /src/docker/e2e/lib.sh
MIG=/opt/code/migrate
MARK='^(<<<<<<< |>>>>>>> )'
S=/srv/s3
P=/srv/pristine
RESULTS=()

section "setup: a v1 remote, its export, and the v2 knowledge repo published from it"
git config --system safe.directory '*'
cp -r /src /opt/code && git -C /opt/code init -q -b main && git -C /opt/code add -A \
  && git -C /opt/code -c user.name=t -c user.email=t@t.invalid commit -q -m code && chmod -R a+rX /opt/code
mkdir -p "$S" "$P" && chmod 1777 "$S" "$P"
asu() { local u="$1"; shift; runuser -u "$u" -- env HOME="/home/$u" USER="$u" CORKERN_DEVICE_NAME="$u-pc" \
          PATH="/home/$u/.local/bin:/usr/local/bin:/usr/bin:/bin" "$@"; }
for u in old new; do asu "$u" git config --global user.name "$u"; asu "$u" git config --global user.email "$u@mig.invalid"; done
asu old git init -q --bare -b main "$P/v1.git"; asu old git init -q --bare -b main "$P/kr.git"
asu old bash -c "set -e; mkdir -p ~/dev/corkern && cd ~/dev/corkern && tar -xf /v1-code.tar \
  && mkdir -p memory/corkern memory/webshop global .devices \
  && printf 'corkern:dev/corkern\nwebshop:dev/projects/webshop\n' > projects.conf \
  && printf -- '- [State](state.md)\n' > memory/corkern/MEMORY.md \
  && printf 'line one\nFACT-base-state\nline three\n' > memory/corkern/state.md \
  && echo FACT-base-grep > memory/webshop/facts.md && echo '# global' > global/CLAUDE.md \
  && printf 'date\thost\tslug\n2026-01-01 10:00:00\told\tcorkern\n' > .metrics.tsv \
  && git init -q -b main && git add -A && git -c commit.gpgsign=false commit -qm base \
  && git remote add origin $P/v1.git && git push -q -u origin main"
chmod -R a+rwX "$P"
check "seed: 1-export-v1.sh" asu old bash "$MIG/1-export-v1.sh" --yes
cp -r /home/old/corkern-migration /home/new/ && chown -R new:new /home/new/corkern-migration
check "seed: 2-setup-new-machine.sh publishes the knowledge repo" \
      asu new bash /home/new/corkern-migration/2-setup-new-machine.sh --remote "$P/kr.git" --code-repo /opt/code --yes
if [ "$FAIL" -gt 0 ]; then tail -30 /home/new/.local/state/corkern-migration/2-setup-new-machine.log; finish; exit 1; fi
chmod -R a+rwX "$P"
BASE=$(git -C "$P/kr.git" show HEAD:.corkern-migration | sed -n 's/^v1_export_commit=//p')
AWS="AKIA""ABCDEFGHIJ234567"     # the fixtures' fake AWS key, split so this file holds no literal

# world NAME — a new v1 machine "user" (own copies of both remotes), v1 cloned and wired by sync.sh
N=0
world() {
    NAME="$1"; W="$S/$1"; N=$((N + 1)); U="sw$N"; FACTS=(FACT-base-state FACT-base-grep); NAMES=()
    useradd -m -s /bin/bash "$U"; mkdir -p "$W"; cp -r "$P/v1.git" "$W/v1.git"; cp -r "$P/kr.git" "$W/kr.git"
    chown -R "$U:$U" "$W"
    asu "$U" git config --global user.name "$U"; asu "$U" git config --global user.email "$U@mig.invalid"
    asu "$U" git config --global commit.gpgsign false
    asu "$U" git clone -q "$W/v1.git" "/home/$U/dev/corkern"
    asu "$U" mkdir -p "/home/$U/.claude"   # Claude Code creates it on first start
    asu "$U" bash "/home/$U/dev/corkern/sync.sh" > "$W/sync.out" 2>&1 || { bad "$NAME: v1 sync.sh failed"; sed 's/^/     │ /' "$W/sync.out" | tail -5; }
    asu "$U" git clone -q "$W/v1.git" "$W/other"
    V1="/home/$U/dev/corkern"; KR="/home/$U/.corkern"
}
v1()    { asu "$U" bash -c "cd '$V1' && $1"; }
other() { asu "$U" bash -c "cd '$W/other' && git pull -q --rebase && $1 && git add -A && git commit -qm other && git push -q"; }
want()  { FACTS+=("$@"); }
wiring() { asu "$U" bash -c "cat ~/.claude/settings.json 2>/dev/null; find ~/.claude ~/.local/bin -maxdepth 3 -type l -printf '%p -> %l\n' 2>/dev/null | sort" | sha256sum; }
snap()  { WIRING_BEFORE=$(wiring); }
run3()  { asu "$U" bash "$MIG/3-switch-v1-machine.sh" --remote "$W/kr.git" --code-repo /opt/code --yes > "$W/output" 2>&1; RC=$?; }

# krw "shell" — a teammate commits to this world's published knowledge repo
krw()   { asu "$U" bash -c "set -e; rm -rf '$W/krw'; git clone -q '$W/kr.git' '$W/krw'; cd '$W/krw'; $1; git add -A; git commit -qm teammate; git push -q"; }
kr_base()    { krw "sed -i 's|^v1_export_commit=.*|v1_export_commit=$1|' .corkern-migration"; }
# local_base SHA — this machine ran script 1 (its state holds the export point)
local_base() { asu "$U" bash -c "mkdir -p ~/.local/state/corkern-migration && printf 'var:v1_export_commit=%s\n' '$1' > ~/.local/state/corkern-migration/1-export-v1.state"; }
# rerun_carry_over — the carry-over step runs again (as after a crash before it was recorded)
rerun_carry_over() { asu "$U" sed -i '/^step:carry-over=/d' "/home/$U/.local/state/corkern-migration/3-switch-v1-machine.state"; run3; }
# own_lines FILE LINE... — every LINE is in FILE exactly once, as a line of its own
own_lines() { local f="$1" l; shift; for l in "$@"; do [ "$(grep -cxF -- "$l" "$f")" = 1 ] || { echo "not once on its own line: $l" >&2; return 1; }; done; }

fail() { bad "$NAME: $1"; FAILED_HERE=1; }
verdict() {   # verdict ok|refuse "what this scenario is" [pattern]
    local want="$1" desc="$2" pat="${3:-}" got=ok f
    FAILED_HERE=0
    [ "$RC" -ne 0 ] && got=refuse
    grep -q '^Traceback (most recent call last)' "$W/output" && fail "Python traceback"
    [ "$want" != "$got" ] && fail "expected $want, got $got"
    [ -n "$pat" ] && ! grep -qiE -- "$pat" "$W/output" && fail "output lacks /$pat/"
    [ -d "$KR" ] && grep -rlE --exclude-dir=.git "$MARK" "$KR" >/dev/null 2>&1 && fail "conflict markers in the knowledge repo"
    if [ "$got" = ok ]; then
        { [ -d "/home/$U/dev/corkern-v1-retired" ] && [ ! -e "$V1" ]; } || fail "v1 not retired"
        for f in "${FACTS[@]}"; do grep -rqF --exclude-dir=.git -- "$f" "$KR" || fail "v2 lost $f"; done
        for f in "${NAMES[@]}"; do [ -f "$KR/$f" ] || fail "not carried over under its own name: $f"; done
        [ "$(asu "$U" git -C "$KR" rev-parse HEAD)" = "$(asu "$U" git -C "$KR" ls-remote origin HEAD | cut -f1)" ] || fail "knowledge repo not pushed"
    else
        grep -qE 'fix:|resumes here' "$W/output" || fail "refused without saying how to fix it"
        if grep -q 'nothing was changed' "$W/output"; then
            { [ -d "$V1/.git" ] && [ ! -e "/home/$U/dev/corkern-v1-retired" ]; } || fail "v1 was retired although the script refused"
            [ "$(wiring)" = "$WIRING_BEFORE" ] || fail "v1's Claude Code wiring changed although nothing was supposed to"
        fi
        local repo="$V1"; [ -d "$repo" ] || repo="/home/$U/dev/corkern-v1-retired"
        asu "$U" git -C "$repo" log --all -p --diff-merges=first-parent > "$W/history" 2>&1
        for f in "${FACTS[@]}"; do
            grep -rqF --exclude-dir=.git -- "$f" "$repo" || grep -qF -- "$f" "$W/history" || fail "refused but $f is gone"
        done
    fi
    if [ "$FAILED_HERE" = 0 ]; then ok "$NAME: $desc → $got"
    else sed 's/^/     │ /' "$W/output" | grep -vE '^\s+│\s*$' | tail -14; fi
    RESULTS+=("$(printf '%-24s %-7s %-7s %s' "$NAME" "$want" "$got" "$([ "$FAILED_HERE" = 0 ] && echo ✅ || echo ❌)")")
}

section "switching a second v1 machine"
world clean;       v1 "echo FACT-after > memory/webshop/after.md && git add -A && git commit -qm after"; want FACT-after; snap; run3
                   verdict ok "v1 wrote a memory after the export"
world uncommitted; v1 "echo FACT-unc >> memory/corkern/state.md"; want FACT-unc; snap; run3; verdict ok "uncommitted memory at the switch"
world from-other;  other "echo FACT-oth > memory/webshop/oth.md"; v1 "echo FACT-mine > memory/webshop/mine.md"; want FACT-oth FACT-mine
                   snap; run3; verdict ok "another v1 machine pushed meanwhile"
world index-both;  other "echo '- [Theirs](FACT-i1.md)' >> memory/corkern/MEMORY.md"; v1 "echo '- [Ours](FACT-i2.md)' >> memory/corkern/MEMORY.md"
                   want FACT-i1 FACT-i2; snap; run3; verdict ok "both appended a MEMORY.md line"
world local-paths; v1 "echo 'webshop:/mnt/c/FACT-local' > projects.local.conf"; want FACT-local; snap; run3
                   verdict ok "machine-specific paths (projects.local.conf)"
assert "local-paths: kept in corkern.conf.local, not committed" bash -c "grep -q FACT-local '$KR/corkern.conf.local' && ! git -C '$KR' ls-files --error-unmatch corkern.conf.local >/dev/null 2>&1"
world names;       v1 "echo FACT-u1 > 'memory/webshop/ünïcødé.md' && echo FACT-u2 > 'memory/webshop/with space.md' && git add -A && git commit -qm names"
                   want FACT-u1 FACT-u2; NAMES=("memory/webshop/ünïcødé.md" "memory/webshop/with space.md"); snap; run3
                   verdict ok "unicode and spaces in names written after the export"
world uu-registry; other "echo 'crm:/mnt/c/dev/crm' >> projects.conf"; v1 "echo 'wiki:/mnt/c/FACT-uu' >> projects.conf"
                   asu "$U" bash -c "cd '$V1' && git stash -q && git pull -q --rebase && git stash pop -q" >/dev/null 2>&1
                   want FACT-uu; snap; run3; verdict refuse "failed autostash pop (half-merged)" "half-merged"
world stash-only;  v1 "echo FACT-st >> memory/corkern/state.md && git stash -q"; want FACT-st; snap; run3; verdict refuse "memories only in a stash" "stash"
world memory-clash; other "sed -i 's/line one/FACT-mc-theirs/' memory/corkern/state.md"; v1 "sed -i 's/line one/FACT-mc-ours/' memory/corkern/state.md"
                   want FACT-mc-theirs FACT-mc-ours; snap; run3; verdict refuse "both machines changed the same memory line" "conflict in"
assert "memory-clash: v1 not retired, not mid-rebase" bash -c "[ -d '$V1/.git' ] && [ ! -d '$V1/.git/rebase-merge' ] && [ ! -e /home/$U/dev/corkern-v1-retired ]"
world detached;    v1 "git checkout -q --detach"; snap; run3; verdict refuse "detached HEAD" "detached"
world index-lock;  v1 "touch .git/index.lock"; snap; run3; verdict refuse "stale index.lock" "index.lock"
world kr-gone;     snap; asu "$U" bash "$MIG/3-switch-v1-machine.sh" --remote /nonexistent/kr.git --code-repo /opt/code --yes > "$W/output" 2>&1; RC=$?
                   verdict refuse "knowledge repo not reachable" "reachable"
world push-fails;  v1 "echo FACT-pf > memory/webshop/pf.md"; want FACT-pf; chmod -R a-w "$W/kr.git"; snap; run3
                   verdict refuse "knowledge repo rejects the push (mid-run)" "resumes here"
                   chmod -R u+w "$W/kr.git"; run3; verdict ok "…rerun resumes and finishes"

section "carry-over: the export point is verified (audit L15)"
world base-head;    kr_base HEAD; snap; run3; verdict refuse "synced export point 'HEAD'" "export point"
assert "base-head: v1 not retired" test -d "$V1/.git"
world base-option;  kr_base --output=x; snap; run3; verdict refuse "synced export point '--output=x'" "export point"
assert "base-option: v1 not retired" test -d "$V1/.git"
world base-foreign; kr_base "$(v1 'git commit-tree -m foreign HEAD^{tree}')"; snap; run3
                    verdict refuse "export point not an ancestor of v1's history" "not an ancestor"
assert "base-foreign: v1 not retired" test -d "$V1/.git"
world base-unknown; kr_base 0123456789abcdef0123456789abcdef01234567; snap; run3
                    verdict refuse "export point this v1 does not have" "fetch v1 first"
assert "base-unknown: v1 not retired" test -d "$V1/.git"
world base-differs; local_base "$(v1 'git commit-tree -m mine HEAD^{tree}')"; snap; run3
                    verdict refuse "script 1's export point here differs from the synced one" "differs.*$BASE"
assert "base-differs: v1 not retired" test -d "$V1/.git"
world base-matches; local_base "$BASE"; v1 "echo FACT-lm > memory/webshop/lm.md && git add -A && git commit -qm lm"; want FACT-lm
                    snap; run3; verdict ok "script 1's export point here matches the synced one"
world nothing-new;  snap; run3; verdict ok "v1 wrote nothing since the export"
assert "nothing-new: says there was nothing to carry over" grep -q "nothing to carry over" "$W/output"
assert "nothing-new: does not call differences expected" bash -c "! grep -q 'Differing files are expected' '$W/output'"

section "carry-over: never writes through a symlink (audit L13)"
world symlink;      v1 "printf -- '- [Late](FACT-late.md)\n' >> memory/corkern/MEMORY.md && mkdir -p memory/newslug && echo FACT-ns > memory/newslug/ns.md && git add -A && git commit -qm late"
                    want FACT-late FACT-ns; snap; run3; verdict ok "first switch (before the links appear)"
# the work tree gets links (e.g. a clone made by an older corkern, before core.symlinks=false) and
# the carry-over runs again: MEMORY.md -> a file outside, memory/newslug -> a folder outside
asu "$U" bash -c "set -e; printf 'outside\n' > ~/outside.txt; mkdir -p ~/outside-dir; cd '$KR'
  rm memory/corkern/MEMORY.md && ln -s ~/outside.txt memory/corkern/MEMORY.md
  rm -r memory/newslug && ln -s ~/outside-dir memory/newslug
  git add -A && git commit -qm links"
OUTSIDE=$(asu "$U" bash -c 'sha256sum ~/outside.txt; ls -A ~/outside-dir')
rerun_carry_over
assert "symlink: the rerun finishes" test "$RC" = 0
assert "symlink: the files outside the repo are unchanged" test "$(asu "$U" bash -c 'sha256sum ~/outside.txt; ls -A ~/outside-dir')" = "$OUTSIDE"
assert "symlink: both skips are reported" bash -c "grep -q 'skipped (symlink.*memory/corkern/MEMORY.md' '$W/output' && grep -q 'skipped (symlink.*memory/newslug/ns.md' '$W/output'"
assert "symlink: v1 unchanged (retired, every fact kept)" bash -c "grep -rqF FACT-late /home/$U/dev/corkern-v1-retired/memory && grep -rqF FACT-ns /home/$U/dev/corkern-v1-retired/memory"
world symlink-remote; krw "rm -r memory/webshop && ln -s /home/$U/outside-dir memory/webshop"
                    v1 "echo FACT-sr > memory/webshop/sr.md && git add -A && git commit -qm sr"
                    asu "$U" mkdir -p "/home/$U/outside-dir"; snap; run3
assert "symlink-remote: the switch finishes" test "$RC" = 0
assert "symlink-remote: nothing written outside the repo" test -z "$(asu "$U" ls -A "/home/$U/outside-dir")"
assert "symlink-remote: the cloned link is no link (core.symlinks=false)" test ! -L "$KR/memory/webshop"
assert "symlink-remote: the skip is reported" grep -q "skipped (.*memory/webshop/sr.md" "$W/output"

section "carry-over: no lost last line (audit L16)"
world no-newline;   krw "printf -- '- [KR only](FACT-kr-only.md)' >> memory/corkern/MEMORY.md && printf 'krproj:/mnt/c/FACT-krp' >> corkern.conf"
                    v1 "printf -- '- [V1 one](FACT-nl1.md)\n- [V1 last](FACT-nl-last.md)' >> memory/corkern/MEMORY.md \
                        && printf 'p1:/mnt/c/FACT-p1\np2:/mnt/c/FACT-p2' >> projects.conf && git add -A && git commit -qm nl \
                        && printf 'webshop:/mnt/c/FACT-l1\ncrm:/mnt/c/FACT-l2' > projects.local.conf"
                    want FACT-kr-only FACT-nl1 FACT-nl-last FACT-p1 FACT-p2 FACT-l1 FACT-l2; snap; run3
                    verdict ok "MEMORY.md, projects.conf, projects.local.conf without a final newline (both sides)"
assert "no-newline: MEMORY.md — every line, each on its own" own_lines "$KR/memory/corkern/MEMORY.md" \
       "- [State](state.md)" "- [KR only](FACT-kr-only.md)" "- [V1 one](FACT-nl1.md)" "- [V1 last](FACT-nl-last.md)"
assert "no-newline: corkern.conf — every line, each on its own" own_lines "$KR/corkern.conf" \
       "krproj:/mnt/c/FACT-krp" "p1:/mnt/c/FACT-p1" "p2:/mnt/c/FACT-p2"
assert "no-newline: corkern.conf.local — every line, each on its own" own_lines "$KR/corkern.conf.local" \
       "webshop:/mnt/c/FACT-l1" "crm:/mnt/c/FACT-l2"
assert "no-newline: the report says combined" grep -q "combined lines: memory/corkern/MEMORY.md" "$W/output"
asu "$U" bash -c "truncate -s -1 '$KR/corkern.conf.local' && printf '\nwiki:/mnt/c/FACT-l3' >> /home/$U/dev/corkern-v1-retired/projects.local.conf"
SUMS=$(sha256sum "$KR/memory/corkern/MEMORY.md" "$KR/corkern.conf")
rerun_carry_over
assert "no-newline: rerun finishes" test "$RC" = 0
assert "no-newline: rerun adds nothing to MEMORY.md / corkern.conf" test "$(sha256sum "$KR/memory/corkern/MEMORY.md" "$KR/corkern.conf")" = "$SUMS"
assert "no-newline: rerun — corkern.conf.local without a final newline gets the new line on its own" \
       own_lines "$KR/corkern.conf.local" "webshop:/mnt/c/FACT-l1" "crm:/mnt/c/FACT-l2" "wiki:/mnt/c/FACT-l3"

section "carry-over: scanned for secrets (audit L14)"
world secret;       v1 "echo 'FACT-sec deploy with $AWS' > memory/webshop/sec.md && git add -A && git commit -qm sec"; want FACT-sec
                    snap; run3; verdict ok "a secret written in v1 after the export"
assert "secret: carried over redacted" grep -q "secret removed by corkern" "$KR/memory/webshop/sec.md"
assert "secret: not in the knowledge repo's files or history" bash -c "! grep -rqF '$AWS' --exclude-dir=.git '$KR' && ! git -C '$KR' log --all -p | grep -qF '$AWS'"
assert "secret: never reaches the remote" bash -c "! git -C '$W/kr.git' log --all -p | grep -qF '$AWS'"
assert "secret: the original is in this machine's quarantine" bash -c "grep -rqF '$AWS' /home/$U/.local/state/corkern/quarantine"
# a commit that bypassed the scan (made by hand, or by an older script) is still waiting to be
# pushed when the script runs again from the scan step: the push never happens
asu "$U" bash -c "cd '$KR' && echo 'late $AWS' > memory/webshop/late.md && git add -A && git -c commit.gpgsign=false commit -qm late"
asu "$U" sed -i '/^step:\(scan\|push\|init-device\|iwe\|verify\)=/d' "/home/$U/.local/state/corkern-migration/3-switch-v1-machine.state"
run3
assert "secret: a secret in an unpushed commit stops the script at the scan step" bash -c "[ '$RC' != 0 ] && grep -q 'holds a secret' '$W/output'"
assert "secret: …and never reaches the remote" bash -c "! git -C '$W/kr.git' log --all -p | grep -qF '$AWS'"

section "summary"
printf '  %-24s %-7s %-7s\n' scenario wanted got
printf '  %s\n' "${RESULTS[@]}"
finish
