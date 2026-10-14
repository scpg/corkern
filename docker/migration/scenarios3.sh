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

section "summary"
printf '  %-24s %-7s %-7s\n' scenario wanted got
printf '  %s\n' "${RESULTS[@]}"
finish
