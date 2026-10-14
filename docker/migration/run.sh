#!/usr/bin/env bash
# The migration scripts (migrate/) end to end, as a user would run them:
#   old = a machine with a REAL corkern v1 (code from origin/main at /v1-code.tar, installed by
#         v1's own sync.sh, uncommitted v1 data, v1's IWE server registered)
#   new = a fresh machine
# 1-export on old → 2-setup on new (incl. a failed precondition and a mid-run failure + resume)
# → both sides write during "UAT" (incl. the same file) → 3-switch on old → carry-over checks →
# trust between the two → rollback on old → v1 is back exactly as before.
# Optional: /real-v1-export.tar (the user's real v1 data, local runs only) instead of test data.
set -uo pipefail
# shellcheck source=docker/e2e/lib.sh
. /src/docker/e2e/lib.sh
as() { local u="$1"; shift; runuser -u "$u" -- env HOME="/home/$u" USER="$u" CORKERN_DEVICE_NAME="$u-pc" \
         PATH="/home/$u/.local/bin:/usr/local/bin:/usr/bin:/bin" "$@"; }
c2() { local u="$1"; shift; as "$u" env CORKERN_MEMORY_DIR="/home/$u/.corkern" corkern "$@"; }
MIG=/opt/code/migrate
V1=/home/old/dev/corkern
KR_REMOTE=/srv/remotes/knowledge.git

section "setup: code, remotes, a real v1 on 'old'"
git config --system safe.directory '*'
cp -r /src /opt/code && git -C /opt/code init -q -b main && git -C /opt/code add -A \
  && git -C /opt/code -c user.name=t -c user.email=t@t.invalid commit -q -m code && chmod -R a+rX /opt/code
mkdir -p /srv/remotes && git init -q --bare --shared=0777 -b main /srv/remotes/v1.git \
  && git init -q --bare --shared=0777 -b main "$KR_REMOTE" && chmod -R a+rwX /srv/remotes
for u in old new; do as "$u" git config --global user.name "$u"; as "$u" git config --global user.email "$u@mig.invalid"; done
as old mkdir -p "$V1" /home/old/dev/projects/webshop /home/old/dev/projects/blogapp
as old tar -xf /v1-code.tar -C "$V1"
if [ -f /real-v1-export.tar ]; then
    as old tar -xf /real-v1-export.tar -C "$V1"; echo "  using the REAL v1 data"
else
    as old bash -c "cd $V1 && mkdir -p memory/{corkern/sessions,webshop,global} .devices global \
      && printf 'corkern:dev/corkern\nwebshop:dev/projects/webshop\nhome:\n' > projects.conf \
      && printf -- '- [State](project_state.md)\n' > memory/corkern/MEMORY.md \
      && printf -- '---\nname: state\n---\nv1 project state\n' > memory/corkern/project_state.md \
      && printf -- '- [ ] 2026-09-01 water the plants\n' > memory/global/todo.md \
      && echo '- a session note' > memory/corkern/sessions/2026-09-20-1000.md \
      && echo 'webshop facts' > memory/webshop/facts.md && echo '# Global (v1)' > global/CLAUDE.md"
fi
as old bash -c "cd $V1 && { [ -f .metrics.tsv ] || printf 'date\thost\tslug\tmem_files\tmem_bytes\tai_bytes\tcontext_pct\n2026-06-01 10:00:00\told-pc\tcorkern\t1\t1\t1\t0\n' > .metrics.tsv; } \
  && printf 'shared before the export\n' > memory/corkern/migration_shared.md \
  && printf -- '- [Shared](migration_shared.md)\n' >> memory/corkern/MEMORY.md \
  && git init -q -b main && git add -A && git -c commit.gpgsign=false commit -qm 'v1 state' \
  && git remote add origin /srv/remotes/v1.git && git push -q -u origin main"
check  "v1 installed by its own sync.sh" as old bash "$V1/sync.sh"
as old claude mcp add iwe --scope user -- "$V1/scripts/iwec-corkern.sh" >/dev/null 2>&1
as old bash -c "cd $V1 && echo 'written but not committed yet' > memory/webshop/uncommitted.md \
  && echo 'blogapp:dev/projects/blogapp' >> projects.conf && echo scratch > scratch-not-data.txt \
  && printf '2026-10-02 09:00:00\told-pc\twebshop\t2\t2\t2\t0\n' >> .metrics.tsv"
# A second v1 machine (like host-b) pushed meanwhile: a memory file and a metrics row of its own
as old bash -c "git clone -q /srv/remotes/v1.git /tmp/host-b && cd /tmp/host-b && echo 'only on host-b' > memory/webshop/from-host-b.md \
  && printf '2026-06-25 19:14:53\thost-b\twebshop\t3\t3\t3\t0\n' >> .metrics.tsv && git add -A \
  && git -c commit.gpgsign=false commit -qm 'host-b memories' && git push -q"
expect "v1 wired: hooks in settings.json" "corkern.sh corkern-pull" cat /home/old/.claude/settings.json
assert "v1 wired: CLAUDE.md → v1"  test "$(readlink /home/old/.claude/CLAUDE.md)" = "$V1/global/CLAUDE.md"
as old cp /home/old/.claude/settings.json /tmp/v1-settings-before.json
as old bash -c "find ~/.claude ~/.local/bin -maxdepth 3 -type l -printf '%p -> %l\n' | sort" > /tmp/v1-links-before.txt
echo "  v1 links: $(wc -l < /tmp/v1-links-before.txt)"

section "1 — a half-merged v1 (failed autostash pop) is refused"
as old bash -c "git clone -q /srv/remotes/v1.git /tmp/host-b2 && cd /tmp/host-b2 && echo 'crm:/mnt/c/dev/crm' >> projects.conf \
  && git -c commit.gpgsign=false commit -qam 'host-b: crm' && git push -q"
as old bash -c "cd $V1 && git stash -q && git pull -q --rebase && git stash pop -q" >/dev/null 2>&1
expect "v1 is half-merged now (UU, no MERGE_HEAD)" "UU projects.conf" as old git -C $V1 status --porcelain
refuse "1-export-v1.sh refuses before changing anything" "half-merged files or conflict markers" as old bash $MIG/1-export-v1.sh --yes
assert "…nothing was committed" bash -c "[ \"\$(git -C $V1 rev-parse HEAD)\" = \"\$(git -C /srv/remotes/v1.git rev-parse main)\" ]"
as old bash -c "cd $V1 && for f in \$(git diff --name-only --diff-filter=U); do sed -i -E '/^(<<<<<<< |=======\$|>>>>>>> )/d' \$f; git add \$f; done && git stash drop -q"
expect "resolved by keeping both sides" "crm:" cat $V1/projects.conf
expect "…both sides kept"               "blogapp:" cat $V1/projects.conf
# the other machine pushes again → step 2 must still rebase over it (metrics row on both sides)
as old bash -c "cd /tmp/host-b2 && git pull -q --rebase && printf '2026-06-26 08:00:00\thost-b\twebshop\t4\t4\t4\t0\n' >> .metrics.tsv \
  && git -c commit.gpgsign=false commit -qam 'host-b metrics again' && git push -q"

section "1 — export on old"
check  "1-export-v1.sh --dry-run only checks" as old bash $MIG/1-export-v1.sh --dry-run
assert "…and changed nothing" test -n "$(as old git -C $V1 status --porcelain)"
check  "1-export-v1.sh (after the fix, --restart)" as old bash $MIG/1-export-v1.sh --yes --restart
assert "pending v1 data was committed"   test -z "$(as old git -C $V1 status --porcelain -- memory projects.conf)"
expect "the other machine's push is in the export" "memory/webshop/from-host-b.md" tar -tf /home/old/corkern-migration/v1-export.tar
expect "metrics conflict resolved: host-b's row kept"  "host-b" cat "$V1/.metrics.tsv"
expect "…and this machine's row kept"                "2026-10-02 09:00:00" cat "$V1/.metrics.tsv"
assert "no rebase left behind, v1 pushed"           bash -c "! [ -d $V1/.git/rebase-merge ] && [ \"\$(git -C $V1 rev-parse HEAD)\" = \"\$(git -C /srv/remotes/v1.git rev-parse main)\" ]"
assert "code/scratch files left alone"    test -n "$(as old git -C $V1 status --porcelain -- scratch-not-data.txt)"
expect "bundle checksum ok"               "OK" as old bash -c "cd ~/corkern-migration && sha256sum -c v1-export.tar.sha256"
expect "manifest has the export point"    "v1_export_commit=$(as old git -C $V1 rev-parse HEAD)" cat /home/old/corkern-migration/manifest.txt
assert "scripts packed for the next step" test -f /home/old/corkern-migration/2-setup-new-machine.sh
expect "rerun: every step already done"   "(done " as old bash $MIG/1-export-v1.sh --yes
expect "log written"                      "step done: pack" cat /home/old/.local/state/corkern-migration/1-export-v1.log
if [ -f "$V1/export-import.py" ]; then
    expect "v1 has 'corkern export': the bundle is a verified corkern export" "format=corkern-export" cat /home/old/corkern-migration/manifest.txt
    expect "corkern import --check on v1 itself: all identical" "checksums match" as old "$V1/corkern" import /home/old/corkern-migration/v1-export.tar --check
else
    echo "  (v1 under test has no corkern export — the git-archive fallback is tested)"
fi

section "2 — new machine"
cp -r /home/old/corkern-migration /home/new/ && chown -R new:new /home/new/corkern-migration
B=/home/new/corkern-migration
refuse "preconditions: no publish target → stops, explains" "--repo" as new bash $B/2-setup-new-machine.sh --code-repo /opt/code --yes
assert "…nothing installed" test ! -e /home/new/.corkern -a ! -e /home/new/.local/bin/corkern
chmod 0555 "$KR_REMOTE" "$KR_REMOTE"/objects "$KR_REMOTE"/refs/heads
refuse "a step fails (remote read-only) → stops at that step" "resumes here" \
       as new bash $B/2-setup-new-machine.sh --remote "$KR_REMOTE" --code-repo /opt/code --yes
expect "progress kept: install done, publish pending" "✓ install" as new bash $B/2-setup-new-machine.sh --status
chmod -R a+rwX "$KR_REMOTE"
check  "rerun after the fix → resumes and finishes" as new bash $B/2-setup-new-machine.sh --remote "$KR_REMOTE" --code-repo /opt/code --yes
expect "…install was not repeated" "Install corkern v2.*(done " as new bash $B/2-setup-new-machine.sh --remote "$KR_REMOTE" --code-repo /opt/code --yes
expect "migrated memory present"      "v1 project state\|." cat /home/new/.corkern/memory/corkern/migration_shared.md
expect "uncommitted v1 data made it"  "written but not committed yet" cat /home/new/.corkern/memory/webshop/uncommitted.md
expect "export point recorded"        "v1_export_commit=" cat /home/new/.corkern/.corkern-migration
expect "published"                    "corkern: record the v1 export point" git -C "$KR_REMOTE" log --format=%s
expect "hooks wired (v2)"             "bash \"\$f\" corkern-session-start" jq -r '.hooks.SessionStart[].hooks[].command' /home/new/.claude/settings.json
assert "settings.json is machine-neutral (no /home/new, no env)" bash -c "! grep -q '/home/new' /home/new/.claude/settings.json"
expect "CLAUDE.md import is machine-neutral" "@~/.corkern/global/CLAUDE.md" cat /home/new/.claude/CLAUDE.md
expect "machine.conf knows this machine's repo" "memory_dir=/home/new/.corkern" cat /home/new/.config/corkern/machine.conf
expect "pull verification on"         "ON" c2 new trust
assert "shell profile: no machine path" bash -c "! grep -qs CORKERN_MEMORY_DIR /home/new/.bashrc"
expect "status.sh: migrated"          "This machine is migrated" as new bash $B/status.sh
assert "the sync test left no reminder behind" bash -c "! grep -qs 'corkern migration check' /home/new/.corkern/memory/global/todo.md"
assert "…and its removal reached the remote" bash -c "[ \"\$(git -C /home/new/.corkern rev-parse HEAD)\" = \"\$(git -C $KR_REMOTE rev-parse main)\" ]"
if [ -f "$V1/export-import.py" ]; then
    expect "2 confirmed the migration against the export" "every file of the export is in the knowledge repo" \
        cat /home/new/.local/state/corkern-migration/2-setup-new-machine.log
    expect "corkern import --check: nothing missing" "identical" c2 new import $B/v1-export.tar --check
fi

section "UAT period: both sides write, including the same file"
as new bash -c "cd ~/.corkern && echo 'from the new laptop' > memory/webshop/from-new.md \
  && echo 'changed on the NEW laptop' > memory/corkern/migration_shared.md \
  && echo '- [From new](from-new.md)' >> memory/corkern/MEMORY.md"
as new env CORKERN_MEMORY_DIR=/home/new/.corkern CORKERN_HOOK_FOREGROUND=1 bash /home/new/.local/share/corkern/hooks/corkern.sh corkern-push <<< '{}'
as old bash -c "cd $V1 && echo 'from the old machine (v1)' > memory/webshop/from-old.md \
  && echo 'changed on the OLD machine (v1)' > memory/corkern/migration_shared.md \
  && echo '- [From old](from-old.md)' >> memory/corkern/MEMORY.md && git add -A memory \
  && git -c commit.gpgsign=false commit -qm 'v1 during UAT' && echo 'newproj:dev/projects/newproj' >> projects.conf"

section "3 — switch old from v1 to v2"
refuse "preconditions: a running Claude session stops it" "close all Claude Code sessions" \
       bash -c "ln -sf /bin/sleep /tmp/claude; runuser -u old -- /tmp/claude 30 & sleep 1; runuser -u old -- env HOME=/home/old bash $MIG/3-switch-v1-machine.sh --remote $KR_REMOTE --code-repo /opt/code --yes; r=\$?; pkill -u old -x claude; exit \$r"
check  "3-switch-v1-machine.sh" as old bash $MIG/3-switch-v1-machine.sh --remote "$KR_REMOTE" --code-repo /opt/code --yes
assert "v1 repo kept as corkern-v1-retired" test -d /home/old/dev/corkern-v1-retired -a ! -e "$V1"
assert "v1 links removed"                   bash -c "! [ -e /home/old/.claude/corkern.sh ] && ! [ /home/old/.local/bin/corkern -ef /home/old/dev/corkern-v1-retired/corkern ]"
expect "…~/.local/bin/corkern is v2 now"     "corkern 2" /home/old/.local/bin/corkern --version
expect "v2 hooks replaced v1's"             "share/corkern/hooks/corkern.sh" cat /home/old/.claude/settings.json
assert "…no v1 hook left"                   bash -c "! grep -q '.claude/corkern.sh' /home/old/.claude/settings.json"
expect "v1-only file carried over"          "from the old machine" cat /home/old/.corkern/memory/webshop/from-old.md
expect "new laptop's file present"          "from the new laptop" cat /home/old/.corkern/memory/webshop/from-new.md
expect "both changed: the laptop's version stays…" "NEW laptop" cat /home/old/.corkern/memory/corkern/migration_shared.md
expect "…and v1's is kept next to it"       "OLD machine" bash -c "cat /home/old/.corkern/memory/corkern/migration_shared.conflict-v1-*.md"
expect "MEMORY.md has both new lines (a)"   "From old" cat /home/old/.corkern/memory/corkern/MEMORY.md
expect "MEMORY.md has both new lines (b)"   "From new" cat /home/old/.corkern/memory/corkern/MEMORY.md
expect "project added on v1 carried over"   "newproj:dev/projects/newproj" cat /home/old/.corkern/corkern.conf
expect "report says what happened"          "BOTH CHANGED" cat /home/old/.local/state/corkern-migration/carry-over-report.txt
refuse "IWE: v1's server unregistered"      "No MCP server" as old claude mcp get iwe
expect "status -v ran clean of problems"    "0 problem(s)\|All good" c2 old status -v
if [ -f /home/old/dev/corkern-v1-retired/export-import.py ]; then
    assert "3 made a final v1 export"       test -f /home/old/.local/state/corkern-migration/v1-final-export.tar
    expect "3 confirmed v2 against it"      "confirmed against the final v1 export" cat /home/old/.local/state/corkern-migration/3-switch-v1-machine.log
fi
FP=$(c2 old signing --show | sed -n 's/.*Fingerprint *: *\(SHA256:[^ ]*\).*/\1/p')
as new env CORKERN_MEMORY_DIR=/home/new/.corkern CORKERN_HOOK_FOREGROUND=1 bash /home/new/.local/share/corkern/hooks/corkern.sh corkern-pull <<< '{}'
expect "new laptop holds old's commits back until trusted" "Held back" c2 new trust
check  "new laptop trusts old's key" c2 new trust --device "$FP" --yes
as new env CORKERN_MEMORY_DIR=/home/new/.corkern CORKERN_HOOK_FOREGROUND=1 bash /home/new/.local/share/corkern/hooks/corkern.sh corkern-pull <<< '{}'
expect "…then receives the carry-over" "from the old machine" cat /home/new/.corkern/memory/webshop/from-old.md
if [ "$FAIL" -gt 0 ]; then
    section "diagnostics"
    c2 new trust; c2 new status | sed -n '/Sync/,/Tools/p'
    as new bash -c "cat ~/.corkern/.git/corkern/last-sync-* ~/.corkern/.git/corkern/held 2>/dev/null; cat ~/.corkern/.git/corkern/trusted_signers; git -C ~/.corkern log --oneline -5 --all --format='%h %G? %GK %an %s'"
fi

section "rollback on old"
check  "rollback-v1-machine.sh" as old bash $MIG/rollback-v1-machine.sh --yes
assert "v1 repo back"                 test -d "$V1" -a ! -e /home/old/dev/corkern-v1-retired
assert "settings.json exactly as before the switch" cmp -s /tmp/v1-settings-before.json /home/old/.claude/settings.json
as old bash -c "find ~/.claude ~/.local/bin -maxdepth 3 -type l -printf '%p -> %l\n' | sort" > /tmp/v1-links-after.txt
assert "every v1 link restored"       bash -c "comm -23 /tmp/v1-links-before.txt /tmp/v1-links-after.txt | (! grep -q .)"
expect "v1's IWE server back"         "iwec-corkern.sh" as old claude mcp get iwe
check  "v1's corkern status works"    as old "$V1/corkern" status
assert "projects no longer point at v2" bash -c "! grep -rqs autoMemoryDirectory /home/old/dev/projects/*/.claude/settings.local.json"
expect "rollback shows v2 memories are kept" "stay in /home/old/.corkern" cat /home/old/.local/state/corkern-migration/rollback-v1-machine.log
finish
