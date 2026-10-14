#!/usr/bin/env bash
# Judge of the 10-device team simulation (see device.sh). Checks, per knowledge repo and device:
# secrets never reach a remote, untrusted pushes never reach a trusting device, no lost writes,
# convergence, index-limit warnings, and that every user-facing message says what to do next.
set -uo pipefail
# shellcheck source=docker/e2e/lib.sh
. /src/docker/e2e/lib.sh
SHARED=/remote; RES=$SHARED/out; ROSTER=/src/docker/teams/roster.txt
git config --global safe.directory '*'
legit=$(grep -vE '^#|intruder' $ROSTER | awk '{print $1}')
alpha=$(grep -vE '^#|intruder' $ROSTER | awk '$3=="alpha"{print $1}')
n_legit=$(echo "$legit" | wc -l)
for _ in $(seq 2400); do [ "$(find $SHARED/flags -name 'done-*' 2>/dev/null | wc -l)" -ge "$n_legit" ] && break; sleep 0.5; done
in_out() { grep -qF -- "$2" "$RES/$1" 2>/dev/null; }

section "10 devices, 3 knowledge repos (alpha: 4 + intruder, beta: 3, gus: 2)"
assert "all $n_legit legit devices finished" test "$(find $SHARED/flags -name 'done-*' | wc -l)" -eq "$n_legit"
assert "the intruder attacked team alpha" test -e $SHARED/flags/attacked
for d in $legit; do echo "  $d: $(tr '\n' ' ' < "$RES/$d-result.txt")"; done
assert "no Python traceback in any user-facing output" bash -c "! grep -l 'Traceback (most recent' $RES/*.txt"
assert "no device stuck mid-rebase/merge" bash -c "! grep -q '^stuck' $RES/*-result.txt"

section "secrets never reach a remote"
all_remote=""
for repo in alpha beta gus; do all_remote+=$(git -C "$SHARED/$repo.git" log --all -p --format=); done
leaked=0 planted=0
while IFS= read -r s; do
    [ -n "$s" ] || continue; planted=$((planted + 1))
    grep -qF -- "$s" <<< "$all_remote" && { echo "     │ LEAKED: ${s:0:12}…"; leaked=$((leaked + 1)); }
done < <(cat $SHARED/planted-*.txt)
assert "none of the $planted planted secrets is in any remote's history" test "$leaked" -eq 0
names=$(for repo in alpha beta gus; do git -C "$SHARED/$repo.git" log --all --name-only --format=; done)
assert ".env files never committed"          bash -c "! grep -qE '(^|/)\.env' <<< \"\$1\"" _ "$names"
assert "key files (id_ed25519) never committed" bash -c "! grep -q 'id_ed25519$' <<< \"\$1\"" _ "$names"
assert "unreadable file with a token (screenshot.png) never committed" bash -c "! grep -q 'screenshot.png' <<< \"\$1\"" _ "$names"
markers=$(grep -c 'secret removed by corkern' <<< "$all_remote")
assert "redacted memories were synced with markers ($markers marker lines)" test "$markers" -gt 0
for kind in github-token openai-key aws-access-key assigned-credential private-key anthropic-key slack-token google-oauth-secret; do
    assert "  marker for $kind reached the remote" grep -qF "secret removed by corkern: $kind" <<< "$all_remote"
done
missing_h=0
while IFS= read -r h; do grep -qF -- "$h" <<< "$all_remote" || { echo "     │ altered: $h"; missing_h=$((missing_h + 1)); }
done < <(cat $SHARED/harmless-*.txt)
assert "harmless look-alikes were synced unchanged (no false positives)" test "$missing_h" -eq 0

section "the user keeps the originals and is told about it"
for d in $legit; do
    bad_perm=$(awk '$1 != 600' "$RES/$d-quarantine.txt" | wc -l)
    kept=0; while IFS= read -r s; do grep -qF -- "$s" "$RES/$d-quarantine-content.txt" && kept=$((kept + 1)); done \
        < <(grep -v '^Pw' "$SHARED/planted-$d.txt" | head -3)
    if [ "$bad_perm" -eq 0 ] && [ "$kept" -ge 2 ] && in_out "$d-secrets.txt" "original:" \
        && in_out "$d-status.txt" "corkern secrets" && in_out "$d-context.txt" "secrets removed before commit" \
        && in_out "$d-hook-problem.txt" "could not be checked"; then
        ok "$d: originals kept (0600), listed by 'secrets', status + session context + hook note tell the user"
    else
        bad "$d: perms!=600:$bad_perm kept:$kept"; sed 's/^/     │ /' "$RES/$d-secrets.txt" | head -5
    fi
done
assert "context tells Claude not to write secrets"      grep -q "do not write secrets into memory" "$RES/ana-laptop-context.txt"
assert "status warns that real secrets must be rotated (secrets --scan explains)" grep -q "rotate" "$RES/ana-laptop-secrets.txt"
assert "the planted history scan finds nothing (exit 0)" bash -c "grep -h '^exit' $RES/*-scan.txt | sort -u | grep -qx 'exit 0'"

section "trust: the intruder's memories never reach a trusting device"
for d in $alpha; do
    r="$RES/$d-result.txt"
    if ! grep -q '^poison' "$r" && grep -q '^held' "$r" && in_out "$d-status.txt" "held back" \
        && in_out "$d-status.txt" "corkern trust" && in_out "$d-trust.txt" "Held back from the remote" \
        && in_out "$d-trust.txt" "--accept" && in_out "$d-context.txt" "memories held back"; then
        ok "$d: poison not integrated; status, trust and session context explain what to do"
    else
        bad "$d"; sed 's/^/     │ /' "$r"
    fi
done
mfp=$(git -C $SHARED/alpha.git show main:.devices/allowed_signers | grep -c mallory || true)
assert "the intruder listed its own key in the synced allowed_signers" test "$mfp" -ge 1
untrusting=0
for d in $alpha; do grep -qx 'trusted 4' "$RES/$d-result.txt" || untrusting=$((untrusting + 1)); done
assert "…but no device trusts it (4 trusted keys on every alpha device)" test "$untrusting" -eq 0
assert "beta (3) and gus (2) trust exactly their own devices" \
    bash -c "grep -qx 'trusted 3' $RES/dan-result.txt && grep -qx 'trusted 2' $RES/gus-server-result.txt"
for d in $alpha; do
    assert "$d: work done after the attack is kept (committed locally, not lost)" \
        grep -qx 'head-has-after-attack 1' "$RES/$d-result.txt"
done

section "no lost writes, convergence"
for repo in alpha beta gus; do
    hist=$(git -C "$SHARED/$repo.git" log -p -m --all --format=)
    devs=$(grep -vE '^#|intruder' $ROSTER | awk -v r=$repo '$3==r{print $1}')
    missing=0 total=0
    for d in $devs; do
        while IFS= read -r tok; do total=$((total + 1)); grep -qF -- "$tok" <<< "$hist" || missing=$((missing + 1)); done < "$SHARED/written-$d.txt"
    done
    assert "$repo: every write before the attack is on the remote ($total writes)" test "$missing" -eq 0
    trees=$(for d in $devs; do cat "$RES/$d-tree-before-attack.txt"; done | sort -u | wc -l)
    assert "$repo: all devices converged to one tree before the attack" test "$trees" -eq 1
done
assert "repos stay separate (no beta memory in alpha)" bash -c "! git -C $SHARED/alpha.git log --all --name-only --format= | grep -q 'memory/beta-shared'"

section "MEMORY.md read limit (beta's shared index grew past 200 lines via merges)"
lines=$(git -C $SHARED/beta.git show main:memory/beta-shared/MEMORY.md | wc -l)
echo "  beta-shared/MEMORY.md on the remote: $lines lines"
for d in dan eva finn; do
    assert "$d: status says entries past the limit are NOT loaded" grep -q "beta-shared: .*NOT loaded" "$RES/$d-status.txt"
    assert "$d: session context asks Claude to shorten it" grep -q "Please shorten it now" "$RES/$d-context.txt"
done
assert "alpha's small index triggers no warning" bash -c "! grep -q 'alpha-shared: ' $RES/ben-status.txt"

section "several projects per device (real git repos) + init-device (autoMemoryDirectory)"
for d in $legit; do
    wrong=$(awk '$2 != "/root/.corkern/memory/"$1 || $3 != "dirty=0"' "$RES/$d-projects.txt")
    n=$(wc -l < "$RES/$d-projects.txt")
    if [ -z "$wrong" ] && [ "$n" -ge 2 ]; then ok "$d: $n projects → own memory folder each; project repos stay clean"
    else bad "$d"; sed 's/^/     │ /' "$RES/$d-projects.txt"; fi
done
leaked_settings=0
for pr in /remote/projects/*.git; do
    git -C "$pr" log --all --name-only --format= | grep -q '\.claude/' && { echo "     │ $(basename "$pr") has .claude/ in its history"; leaked_settings=$((leaked_settings + 1)); }
done
assert "machine-specific settings never committed to any project repo ($(find /remote/projects -maxdepth 1 -name "*.git" | wc -l) repos)" test "$leaked_settings" -eq 0
code_authors=$(git -C /remote/projects/web-app.git log --format=%an | sort -u | wc -l)
echo "  web-app code history has commits from $code_authors people"
assert "ana-desktop (projects under ~/work, mapped in corkern.conf.local) uses the same memory" \
    grep -q "web-app /root/.corkern/memory/web-app dirty=0" "$RES/ana-desktop-projects.txt"
for d in $alpha; do
    assert "$d: session in web-app shows the web-app reminder (from ana-laptop)" \
        grep -q "review the web-app login PR" "$RES/$d-context-webapp.txt"
done
for repo in alpha beta gus; do
    projs=$(awk -v r=$repo '!/^#/ && $2==r {print $1}' /src/docker/teams/projects.txt)
    for proj in $projs; do
        files=$(git -C "$SHARED/$repo.git" ls-tree -r --name-only main -- "memory/$proj/" | grep -c '\-r[0-9]*\.md$')
        assert "$repo/$proj: memories from every team device are synced ($files files)" test "$files" -gt 0
    done
done

section "what the user sees (samples)"
show_file() { echo "  ┌ $1"; sed -n "$3" "$RES/$2" | sed 's/^/  │ /'; }
show_file "ben: corkern trust (after the intruder pushed)" ben-trust.txt '1,30p'
show_file "eva: corkern status — Secrets and trust / Memory indexes" eva-status.txt '/Secrets and trust/,/Tools/p'
show_file "gus-laptop: corkern secrets" gus-laptop-secrets.txt '1,14p'
# shellcheck disable=SC2016  # sed address, not a shell expansion
show_file "carla: session context boxes (what Claude is told)" carla-context.txt '/╔═ Corkern: memories held back/,$p'
show_file "finn: last hook note" finn-hook-problem.txt '1,3p'

touch $SHARED/flags/verified
finish
