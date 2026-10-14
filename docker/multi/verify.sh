#!/usr/bin/env bash
# Judge the 5-device run: no lost writes, convergence, never stuck, index complete.
set -uo pipefail
# shellcheck source=docker/e2e/lib.sh
. /src/docker/e2e/lib.sh
SHARED=/remote; REMOTE=$SHARED/kr.git
git config --global safe.directory '*'
for _ in $(seq 1800); do [ "$(find $SHARED/flags -name 'done-*' 2>/dev/null | wc -l)" -ge 5 ] && break; sleep 0.5; done

section "5 devices — results"
assert "all 5 devices finished" test "$(find $SHARED/flags -name 'done-*' | wc -l)" -eq 5
for n in 1 2 3 4 5; do echo "  dev$n: $(tr '\n' ' ' < "$SHARED/result-dev$n.txt")"; done

section "never stuck, no unresolved errors"
assert "no device stuck mid-rebase/merge" bash -c "! grep -q '^stuck' $SHARED/result-dev*.txt"
assert "no device has uncommitted leftovers" bash -c "! grep -qE '^status [1-9]' $SHARED/result-dev*.txt"
assert "no sync error left after final sync" bash -c "! grep -q '^error' $SHARED/result-dev*.txt"

section "convergence"
remote_tree=$(git -C "$REMOTE" rev-parse 'main^{tree}')
assert "all 5 devices have the remote's exact tree" \
    test "$(grep -h '^tree' $SHARED/result-dev*.txt | sort -u)" = "tree $remote_tree"

section "no lost writes"
history=$(git -C "$REMOTE" log -p -m --all --format=)   # -m: also diffs of merge commits
missing=0 total=0
while IFS= read -r token; do
    total=$((total + 1))
    grep -qF -- "$token" <<< "$history" || { echo "     │ missing: $token"; missing=$((missing + 1)); }
done < <(cat $SHARED/written-dev*.txt)
assert "every write from every device is in the remote history ($total writes)" test "$missing" -eq 0
tree=$(git -C "$REMOTE" ls-tree -r --name-only main)
lost=0 notes=0
for n in 1 2 3 4 5; do
    while IFS= read -r f; do
        r=${f#dev"$n"-r}; r=${r%-unique}; notes=$((notes + 1))
        grep -qx "memory/dev$n/note-$r.md" <<< "$tree" || {
            lost=$((lost + 1)); echo "     │ not in final tree: memory/dev$n/note-$r.md"
            git -C "$REMOTE" log --all --format='     │   %h %an %s' -- "memory/dev$n/note-$r.md" | head -5; }
    done < <(grep -o "dev$n-r[0-9]*-unique" "$SHARED/written-dev$n.txt")
done
assert "every unique note is in the final tree ($notes notes)" test "$lost" -eq 0

section "memory index (union merge)"
index=$(git -C "$REMOTE" show main:memory/shared/MEMORY.md 2>/dev/null)
lines=0 absent=0
while IFS= read -r token; do
    lines=$((lines + 1)); grep -qF -- "$token" <<< "$index" || absent=$((absent + 1))
done < <(grep -h -- '-index$' $SHARED/written-dev*.txt)
assert "shared MEMORY.md holds all $lines index lines from 5 devices" test "$absent" -eq 0
assert "no conflict copies of MEMORY.md" \
    bash -c "! git -C $REMOTE ls-tree -r --name-only main | grep -q 'MEMORY.conflict-'"

section "shared registry and metrics (edited on every device)"
conf=$(git -C "$REMOTE" show main:corkern.conf)
missing_slugs=0
for n in 1 2 3 4 5; do grep -q "^dev$n:" <<< "$conf" || { echo "     │ registry lacks dev$n"; missing_slugs=$((missing_slugs + 1)); }; done
assert "corkern.conf has the project added on each of the 5 devices" test "$missing_slugs" -eq 0
assert "no conflict copies of corkern.conf / .metrics.tsv" \
    bash -c "! git -C $REMOTE ls-tree -r --name-only main | grep -qE '^(corkern|\.metrics)\.conflict-'"
hosts=$(git -C "$REMOTE" show main:.metrics.tsv 2>/dev/null | cut -f2 | sort -u | grep -c '^dev')
assert "metrics rows recorded from several devices ($hosts hosts)" test "$hosts" -ge 2

section "contested file"
copies=$(git -C "$REMOTE" ls-tree -r --name-only main | grep -c 'hot.conflict-' || true)
echo "  hot.md conflict copies on remote: $copies (each = a concurrent edit kept, not lost)"
touch $SHARED/flags/verified
finish
