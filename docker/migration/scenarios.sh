#!/usr/bin/env bash
# 1-export-v1.sh against every v1 repo state we could think of — not the happy path, the states a
# real v1 machine ends up in (failed autostash pops, leftover stashes, half-finished git commands,
# other machines pushing, odd files). Each scenario builds a fresh v1 + remote, runs the script
# like a user would (--yes), and is judged by invariants, not by expected output alone:
#
#   every run     no Python traceback; never leaves conflict markers in v1's HEAD or remote;
#                 never leaves a rebase/merge or index.lock behind; never drops a stash entry
#   success       every FACT planted anywhere (worktree, index, stash that was already applied,
#                 other machines' pushes) is in the export; the export has no conflict markers
#   refusal       says how to fix it; every FACT still exists in v1 (files, stash or history);
#                 "nothing was changed" really means v1 is byte-for-byte as before
#
# shellcheck disable=SC2015  # "a && b || fail" is meant: fail when any part fails
# Each scenario also pins the expected outcome (ok / refuse) so behaviour changes are visible.
set -uo pipefail
# shellcheck source=docker/e2e/lib.sh
. /src/docker/e2e/lib.sh
MIG=/src/migrate
S=/srv/sc
MARK='^(<<<<<<< |>>>>>>> )'
git config --system safe.directory '*'
as() { runuser -u old -- env HOME=/home/old USER=old PATH="/home/old/.local/bin:/usr/local/bin:/usr/bin:/bin" "$@"; }
as git config --global user.name old; as git config --global user.email old@mig.invalid
as git config --global init.defaultBranch main; as git config --global commit.gpgsign false
mkdir -p "$S" && chown old:old "$S"
RESULTS=()

# world NAME — fresh remote + v1 (+ "other": a second v1 machine's clone)
world() {
    NAME="$1"; W="$S/$1"; FACTS=(FACT-base-state FACT-base-grep); PLANTED=0; LOCK_PLANTED=0; EXTRA=(); RUN_ENV=()
    rm -rf "$W"; as mkdir -p "$W/v1"
    as git init -q --bare -b main "$W/remote.git"
    as tar -xf /v1-code.tar -C "$W/v1"
    v1 "mkdir -p memory/corkern memory/webshop global .devices \
        && printf 'corkern:dev/corkern\nwebshop:dev/projects/webshop\n' > projects.conf \
        && printf -- '- [State](state.md)\n' > memory/corkern/MEMORY.md \
        && printf 'line one\nFACT-base-state\nline three\n' > memory/corkern/state.md \
        && echo FACT-base-grep > memory/webshop/facts.md && echo '# global' > global/CLAUDE.md \
        && printf 'date\thost\tslug\n2026-01-01 10:00:00\told\tcorkern\n' > .metrics.tsv \
        && printf 'host=old\nlast_seen=2026-01-01\n' > .devices/old-pc \
        && git init -q -b main && git add -A && git commit -qm base \
        && git remote add origin '$W/remote.git' && git push -q -u origin main"
    as git clone -q "$W/remote.git" "$W/other"
}
v1()    { as bash -c "cd '$W/v1' && $1"; }
other() { as bash -c "cd '$W/other' && git pull -q --rebase && $1 && git add -A && git commit -qm other && git push -q"; }
want()  { FACTS+=("$@"); }
unwant(){ local f n=(); for f in "${FACTS[@]}"; do [ "$f" = "$1" ] || n+=("$f"); done; FACTS=("${n[@]}"); }

fp() {  # everything that defines v1's state, except what a fetch may update (remote refs)
    as bash -c "cd '$W/v1' && { git rev-parse HEAD; git symbolic-ref -q HEAD; git stash list; git ls-files -s; \
        ls -a .git; find . -path ./.git -prune -o -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum; } 2>&1 | sha256sum"
}
snap() { BEFORE=$(fp); STASH_BEFORE=$(as git -C "$W/v1" stash list | wc -l); }
run1() { as env CORKERN="$W/v1" CORKERN_MIGRATION_DIR="$W/state" "${RUN_ENV[@]}" bash "$MIG/1-export-v1.sh" --out "$W/out" --yes "$@" > "$W/output" 2>&1; RC=$?; }

fail() { bad "$NAME: $1"; FAILED_HERE=1; }
# verdict ok|refuse|any "what this scenario is" [pattern the output must contain]
verdict() {
    local want="$1" desc="$2" pat="${3:-}" got=ok f
    FAILED_HERE=0
    [ "$RC" -ne 0 ] && got=refuse
    grep -q '^Traceback (most recent call last)' "$W/output" && fail "Python traceback"
    [ "$want" != any ] && [ "$want" != "$got" ] && fail "expected $want, got $got"
    [ -n "$pat" ] && ! grep -qiE -- "$pat" "$W/output" && fail "output lacks /$pat/"
    # every run
    if [ "$PLANTED" = 0 ]; then
        as git -C "$W/v1" grep -qE "$MARK" HEAD -- memory global projects.conf .metrics.tsv .devices 2>/dev/null \
            && fail "conflict markers committed in v1's HEAD"
        as git -C "$W/remote.git" grep -qE "$MARK" main -- memory global projects.conf .metrics.tsv .devices 2>/dev/null \
            && fail "conflict markers pushed to v1's remote"
    fi
    [ "$LOCK_PLANTED" = 0 ] && [ -e "$W/v1/.git/index.lock" ] && fail "index.lock left behind"
    case "$NAME" in rebase-*|merge-*) ;; *)
        { [ -d "$W/v1/.git/rebase-merge" ] || [ -d "$W/v1/.git/rebase-apply" ]; } && fail "rebase left in progress" ;; esac
    [ "$(as git -C "$W/v1" stash list | wc -l)" -lt "$STASH_BEFORE" ] && fail "a stash entry was dropped"
    if [ "$got" = ok ]; then
        rm -rf "$W/x" && mkdir -p "$W/x" && tar -xf "$W/out/v1-export.tar" -C "$W/x" 2>/dev/null || fail "export not readable"
        for f in "${FACTS[@]}"; do grep -rqF -- "$f" "$W/x" || fail "export lost $f"; done
        grep -rqE "$MARK" "$W/x" && [ "$PLANTED" = 0 ] && fail "export contains conflict markers"
        for f in "${EXTRA[@]}"; do   # byte-identical files: "<path in v1>"
            local e; e=$(find "$W/x" -path "*/$f" -type f | head -1)
            [ -n "$e" ] && cmp -s "$W/v1/$f" "$e" || fail "not byte-identical in the export: $f"
        done
    else
        grep -qE 'fix:|resumes here' "$W/output" || fail "refused without saying how to fix it"
        for f in "${FACTS[@]}"; do
            # all history incl. stashes (merge commits: --diff-merges); not piped into grep -q (pipefail)
            as git -C "$W/v1" log --all -p --diff-merges=first-parent > "$W/history" 2>&1
            grep -rqF --exclude-dir=.git -- "$f" "$W/v1" || grep -qF -- "$f" "$W/history" \
                || fail "refused but $f is gone from v1"
        done
        grep -q 'nothing was changed' "$W/output" && [ "$(fp)" != "$BEFORE" ] && fail "said 'nothing was changed' but v1 changed"
    fi
    if [ "$FAILED_HERE" = 0 ]; then ok "$NAME: $desc → $got"
    else sed 's/^/     │ /' "$W/output" | grep -vE '^\s+│\s*$' | tail -14; fi
    RESULTS+=("$(printf '%-28s %-7s %-7s %s' "$NAME" "$want" "$got" "$([ "$FAILED_HERE" = 0 ] && echo ✅ || echo ❌)")")
}

# conflicting change on "other" (pushed) + uncommitted change of the same line in v1
same_line() { other "sed -i 's/line one/FACT-$NAME-theirs/' memory/corkern/state.md"
              v1 "sed -i 's/line one/FACT-$NAME-ours/' memory/corkern/state.md"; want "FACT-$NAME-theirs" "FACT-$NAME-ours"; }
# v1 sync's way to fail: stash, pull --rebase, stash pop → conflict, stash kept, files half-merged
failed_pop() { as bash -c "cd '$W/v1' && git stash -q && git pull -q --rebase && git stash pop -q" >/dev/null 2>&1; }

section "plain states"
world clean;          snap; run1; verdict ok "clean v1, nothing pending"
world modified;       v1 "echo FACT-mod >> memory/corkern/state.md"; want FACT-mod; snap; run1; verdict ok "uncommitted change"
world untracked;      v1 "mkdir -p memory/newproj && echo FACT-new > memory/newproj/n.md"; want FACT-new; snap; run1; verdict ok "new untracked memory dir"
world staged;         v1 "echo FACT-staged > memory/webshop/s.md && git add memory/webshop/s.md"; want FACT-staged; snap; run1; verdict ok "staged, not committed"
world deleted;        v1 "git rm -q memory/webshop/facts.md"; unwant FACT-base-grep; snap; run1; verdict ok "memory deleted by the user"
world ahead;          v1 "echo FACT-ahead > memory/webshop/a.md && git add -A && git commit -qm a"; want FACT-ahead; snap; run1; verdict ok "local commit not pushed yet"
world empty;          v1 "git rm -rq memory && git commit -qm empty"; FACTS=(); snap; run1; verdict ok "no memories at all"
world local-conf;     v1 "echo 'webshop:/mnt/c/FACT-localconf' > projects.local.conf"; want FACT-localconf; snap; run1; verdict ok "projects.local.conf (machine overrides)"
world v1-noise;       v1 "touch .corkern-v1.sentinel && echo x > .distill-prompt.md && mkdir -p src tests && echo y > src/a.py && echo FACT-noise >> .metrics.tsv"
                      want FACT-noise; snap; run1; verdict ok "like host-a: sentinel, src/, tests/ untracked"
assert "v1-noise: untracked code was not committed" test -z "$(as git -C "$W/v1" ls-files src tests .corkern-v1.sentinel)"
world "with space";   v1 "echo FACT-space >> memory/corkern/state.md"; want FACT-space; snap; run1; verdict ok "v1 path contains a space"

section "odd files"
world names;   v1 "echo FACT-n1 > 'memory/webshop/with space.md' && echo FACT-n2 > 'memory/webshop/ünïcødé.md' && echo FACT-n3 > 'memory/webshop/-dash.md'"
               want FACT-n1 FACT-n2 FACT-n3; snap; run1; verdict ok "spaces, unicode, leading dash"
world crlf;    v1 "printf 'FACT-crlf\r\nsecond\r\n' > memory/webshop/crlf.md"; want FACT-crlf; EXTRA=(memory/webshop/crlf.md); snap; run1; verdict ok "CRLF line endings kept"
world binary;  v1 "head -c 4096 /dev/urandom > memory/webshop/blob.bin"; EXTRA=(memory/webshop/blob.bin); snap; run1; verdict ok "binary file kept byte-identical"
world big;     v1 "{ echo FACT-big; head -c 3000000 /dev/zero | tr '\\0' a; } > memory/webshop/big.md"; want FACT-big; snap; run1; verdict ok "3 MB memory file"
world symlink; echo FACT-OUTSIDE-SECRET > /srv/outside.txt; chmod 644 /srv/outside.txt
               v1 "ln -s /srv/outside.txt memory/webshop/link.md"; snap; run1; verdict any "symlink pointing outside v1"
assert "symlink: the export holds no copy of the link target" bash -c "! grep -rqF FACT-OUTSIDE-SECRET '$W/x' 2>/dev/null"
world doc-markers; v1 "printf 'How a conflict looks:\n<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> branch\n' > memory/corkern/git_notes.md"
                   snap; run1; verdict refuse "a memory that documents conflict markers (refused: can't tell)" "conflict markers"

section "half-finished git operations"
world uu-registry; other "echo 'crm:/mnt/c/dev/crm' >> projects.conf"; v1 "echo 'wiki:/mnt/c/FACT-uu' >> projects.conf"; failed_pop
                   want FACT-uu; snap; run1; verdict refuse "failed autostash pop: projects.conf UU (what happened on host-a)" "half-merged"
world uu-memory;   same_line; failed_pop; snap; run1; verdict refuse "failed autostash pop: memory file UU" "half-merged"
world markers-committed; v1 "printf '<<<<<<< Updated upstream\na\n=======\nb\n>>>>>>> Stashed changes\n' >> projects.conf && git commit -qam broken && git push -q"
                   PLANTED=1; snap; run1; verdict refuse "markers already committed (c9c0139)" "conflict markers"
world markers-untracked; v1 "printf '<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> b\n' > memory/webshop/bad.md"; snap; run1; verdict refuse "markers in an untracked memory file" "conflict markers"
world merge-progress;  same_line; v1 "git commit -qam ours && git pull -q --no-rebase" >/dev/null 2>&1; snap; run1; verdict refuse "merge in progress" "merge"
world rebase-progress; same_line; v1 "git commit -qam ours && git pull -q --rebase" >/dev/null 2>&1; snap; run1; verdict refuse "rebase in progress" "rebase"
world cherry-pick; v1 "git checkout -qb side && sed -i 's/line one/FACT-cp-side/' memory/corkern/state.md && git commit -qam side && git checkout -q main \
                   && sed -i 's/line one/FACT-cp-main/' memory/corkern/state.md && git commit -qam main && git cherry-pick side" >/dev/null 2>&1
                   want FACT-cp-side FACT-cp-main; snap; run1; verdict refuse "cherry-pick in progress" "cherry-pick|in the middle"
world revert;      v1 "sed -i 's/line one/FACT-rv1/' memory/corkern/state.md && git commit -qam r1 && sed -i 's/FACT-rv1/FACT-rv2/' memory/corkern/state.md \
                   && git commit -qam r2 && git revert --no-edit HEAD~1" >/dev/null 2>&1
                   want FACT-rv2; snap; run1; verdict refuse "revert in progress" "revert|in the middle"
world bisect;      v1 "git bisect start -q 2>/dev/null || git bisect start" >/dev/null 2>&1; snap; run1; verdict refuse "bisect in progress" "bisect|in the middle"
world index-lock;  v1 "touch .git/index.lock"; LOCK_PLANTED=1; snap; run1; verdict refuse "stale .git/index.lock (a v1 hook still running?)" "index.lock"

section "stashes"
world stash-unapplied; v1 "echo FACT-stash >> memory/corkern/state.md && git stash -q"; want FACT-stash; snap; run1
                       verdict refuse "memories only in a stash (autostash never popped)" "stash"
world stash-untracked; v1 "echo FACT-stu > memory/webshop/new.md && git stash -q -u"; want FACT-stu; snap; run1
                       verdict refuse "a new memory file only in a stash (-u)" "stash"
world stash-resolved;  other "echo 'crm:/mnt/c/dev/crm' >> projects.conf"; v1 "echo 'wiki:/mnt/c/FACT-res' >> projects.conf"; failed_pop
                       v1 "sed -i -E '/^(<<<<<<< |=======\$|>>>>>>> )/d' projects.conf && git add projects.conf"
                       want FACT-res; snap; run1; verdict ok "failed pop resolved by hand, stash kept (host-a now)" "stash"
world stash-noise;     v1 "echo 'last_seen=2026-09-09' >> .devices/old-pc && echo 'x	y	z' >> .metrics.tsv && git stash -q"
                       snap; run1; verdict ok "stash holds only metrics/device stamps" "stash"

section "branches and remotes"
world detached;     v1 "git checkout -q --detach"; snap; run1; verdict refuse "detached HEAD" "detached"
world other-branch; v1 "git checkout -qb experiment && echo FACT-br >> memory/corkern/state.md"; want FACT-br; snap; run1; verdict refuse "on another branch than main" "branch"
world no-upstream;  v1 "git branch -q --unset-upstream"; snap; run1; verdict refuse "main has no upstream" "upstream"
world no-remote;    v1 "git remote remove origin && echo FACT-nr >> memory/corkern/state.md"; want FACT-nr; snap; run1; verdict ok "no remote at all"
world unreachable;  v1 "git remote set-url origin /nonexistent/remote.git && echo FACT-ur >> memory/corkern/state.md"; want FACT-ur; snap; run1
                    verdict ok "remote unreachable (--yes: export without it)" "not reachable"
world push-rejected; printf '#!/bin/sh\nexit 1\n' > "$W/remote.git/hooks/pre-receive"; chmod +x "$W/remote.git/hooks/pre-receive"
                    v1 "echo FACT-pr >> memory/corkern/state.md"; want FACT-pr; snap; run1; verdict ok "push rejected by the remote" "push failed"
world remote-gone;  as git -C "$W/other" push -q origin --delete main 2>/dev/null; snap; run1; verdict any "remote branch deleted"
world force-pushed; other "git commit -q --amend -m rewritten --allow-empty"; as git -C "$W/other" push -qf 2>/dev/null
                    v1 "echo FACT-fp >> memory/webshop/facts.md"; want FACT-fp; snap; run1; verdict any "remote history rewritten (force push)"
world shallow;      as rm -rf "$W/v1"; as git clone -q --depth 1 "file://$W/remote.git" "$W/v1"
                    other "echo FACT-sh1 > memory/webshop/sh.md"; v1 "echo FACT-sh2 >> memory/corkern/state.md"; want FACT-sh1 FACT-sh2
                    snap; run1; verdict ok "v1 is a shallow clone"

section "other machines pushed meanwhile"
world behind;           other "echo FACT-other > memory/webshop/o.md"; want FACT-other; snap; run1; verdict ok "behind: host-b pushed a memory"
world diverged;         other "echo FACT-d1 > memory/webshop/o.md"; v1 "echo FACT-d2 > memory/webshop/m.md"; want FACT-d1 FACT-d2; snap; run1
                        verdict ok "both sides new files"
world metrics-both;     other "printf '2026-06-25 19:14:53\thost-b\tFACT-m1\n' >> .metrics.tsv"; v1 "printf '2026-10-02 09:00:00\told\tFACT-m2\n' >> .metrics.tsv"
                        want FACT-m1 FACT-m2; snap; run1; verdict ok "both appended metrics rows"
world index-both;       other "echo '- [Theirs](FACT-i1.md)' >> memory/corkern/MEMORY.md"; v1 "echo '- [Ours](FACT-i2.md)' >> memory/corkern/MEMORY.md"
                        want FACT-i1 FACT-i2; snap; run1; verdict ok "both appended a MEMORY.md line (kept both)"
world registry-both;    other "echo 'crm:/mnt/c/FACT-r1' >> projects.conf"; v1 "echo 'wiki:/mnt/c/FACT-r2' >> projects.conf"
                        want FACT-r1 FACT-r2; snap; run1; verdict ok "both registered a project (kept both)"
world registry-clash;   other "sed -i 's|^webshop:.*|webshop:/FACT-rc1|' projects.conf"; v1 "sed -i 's|^webshop:.*|webshop:/FACT-rc2|' projects.conf"
                        want FACT-rc1 FACT-rc2; snap; run1; verdict refuse "same project, two different paths" "projects.conf"
world memory-clash;     same_line; snap; run1; verdict refuse "both changed the same memory line" "conflict in"

section "commits that cannot be made"
world no-identity;  v1 "echo FACT-ni >> memory/corkern/state.md"; want FACT-ni; RUN_ENV=(GIT_CONFIG_GLOBAL=/dev/null); snap; run1
                    verdict refuse "git has no user.name/email" "user.(name|email)"
world sign-fails;   v1 "git config commit.gpgsign true && git config gpg.format ssh && git config user.signingkey /nonexistent.key && echo FACT-sf >> memory/corkern/state.md"
                    want FACT-sf; snap; run1; verdict refuse "commit signing fails (1Password locked)" "sign"
world claude-open;  v1 "echo FACT-co >> memory/corkern/state.md"; want FACT-co; ln -sf /bin/sleep /tmp/claude
                    runuser -u old -- /tmp/claude 60 & CL=$!; sleep 0.5; snap; run1; kill "$CL" 2>/dev/null
                    verdict ok "a Claude Code session is open (warned)" "Claude Code session"

section "reruns"
world rerun;        snap; run1; run1; verdict ok "second run, nothing changed" "\(done "
world stale-resume; run1; v1 "echo FACT-late > memory/webshop/late.md"; want FACT-late; snap; run1
                    verdict refuse "rerun after v1 changed: the old export would be stale" "--restart"
                    snap; run1 --restart; verdict ok "…then --restart makes a fresh export"
world dry-restart;   run1; v1 "echo FACT-dr > memory/webshop/dr.md"; ST=$(sha256sum < "$W/state/1-export-v1.state")
                    snap; run1 --dry-run --restart; verdict ok "--dry-run --restart checks as a restart would…" "nothing was changed"
assert "dry-restart: …and keeps the progress file as it was" bash -c "[ \"\$(sha256sum < '$W/state/1-export-v1.state')\" = '$ST' ] && ! ls '$W/state/'*.old >/dev/null 2>&1"
                    want FACT-dr; snap; run1 --restart; verdict ok "…then the real --restart exports the new memory"
# interrupted: sha256sum fails once (right after the export was written) — like a full disk
world interrupted;  v1 "echo FACT-int >> memory/corkern/state.md"; want FACT-int; as mkdir -p "$W/shim"
                    printf '#!/bin/sh\n[ -e %s/shim/failed ] && exec /usr/bin/sha256sum "$@"\ntouch %s/shim/failed; exit 1\n' "$W" "$W" > "$W/shim/sha256sum"
                    chmod 755 "$W/shim/sha256sum"; RUN_ENV=(PATH="$W/shim:/usr/local/bin:/usr/bin:/bin"); snap; run1
                    verdict refuse "export step fails midway" "resumes here"
                    snap; run1; verdict ok "…rerun resumes and finishes" "\(done "
world out-readonly; snap; as env CORKERN="$W/v1" CORKERN_MIGRATION_DIR="$W/state" bash "$MIG/1-export-v1.sh" --out /proc/nope --yes > "$W/output" 2>&1; RC=$?
                    verdict refuse "output folder not writable" "output folder"
world old-v1;       v1 "git rm -q export-import.py && git commit -qm 'old v1' && git push -q && echo FACT-ov >> memory/corkern/state.md"; want FACT-ov
                    snap; run1; verdict ok "v1 without 'corkern export' (git archive fallback)" "git archive"

section "the machine around v1"
# host-b: v1 on /mnt/c, but Claude wrote session notes into ~/dev/corkern/memory (never synced)
world stray-folder; as mkdir -p /home/old/dev/corkern/memory/webshop/sessions
                    as bash -c "echo FACT-stray > /home/old/dev/corkern/memory/webshop/sessions/2026-06-22-2345.md"
                    snap; run1; verdict refuse "memories in a stray ~/dev/corkern while v1 is elsewhere (host-b)" "stray"
assert "stray-folder: the stray notes are untouched" grep -q FACT-stray /home/old/dev/corkern/memory/webshop/sessions/2026-06-22-2345.md
rm -rf /home/old/dev/corkern

section "summary"
printf '  %-28s %-7s %-7s\n' scenario wanted got
printf '  %s\n' "${RESULTS[@]}"
finish
