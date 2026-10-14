#!/usr/bin/env bash
# One simulated device (runs in its own container). All devices share /remote.
# Phases, coordinated through flag files on the shared volume:
#   setup (dev1 creates + publishes the knowledge repo; others clone)
#   go    (all start together) → ROUNDS of concurrent memory writes through the real hooks
#   final (after everyone pushed): pull, report tree → verify.sh judges
set -uo pipefail
N="$1"; ROUNDS="${ROUNDS:-15}"
SHARED=/remote; REMOTE=$SHARED/kr.git; FLAGS=$SHARED/flags
export CORKERN_CODE_REPO=/opt/code CORKERN_CODE_REF=main
export PATH="$HOME/.local/bin:$PATH" CORKERN_MEMORY_DIR="$HOME/.corkern"
HOOK="$HOME/.local/share/corkern/hooks/corkern.sh"
LOG=$SHARED/log-dev$N.txt
wait_for() { for _ in $(seq 600); do [ -e "$1" ] && return 0; sleep 0.2; done; echo "timeout waiting for $1" >> "$LOG"; exit 1; }
count() { find "$FLAGS" -name "$1-*" | wc -l; }
fire() { echo '{"hook_event_name":"x"}' | bash "$HOOK" "$1"; }
note() { echo "$(date +%T.%N | cut -c1-12) dev$N $*" >> "$LOG"; }

git config --global user.name "dev$N"; git config --global user.email "dev$N@multi.invalid"
git config --global safe.directory '*'
mkdir -p "$FLAGS"
# Every container has its own filesystem → each device builds its own code checkout
rm -rf /opt/code && cp -r /src /opt/code && git -C /opt/code init -q -b main \
    && git -C /opt/code add -A && git -C /opt/code commit -q -m code
if [ "$N" = 1 ]; then
    bash /opt/code/install.sh --new >> "$LOG" 2>&1 || { note "install failed"; exit 1; }
    git init -q --bare --shared=0777 -b main "$REMOTE"
    git -C "$CORKERN_MEMORY_DIR" remote add origin "$REMOTE"
    git -C "$CORKERN_MEMORY_DIR" push -q -u origin main
    touch "$FLAGS/published"
else
    wait_for "$FLAGS/published"
    bash /opt/code/install.sh --clone "$REMOTE" >> "$LOG" 2>&1 || { note "install failed"; exit 1; }
fi
touch "$FLAGS/ready-$N"
while [ "$(count ready)" -lt 5 ]; do sleep 0.2; done

KR=$CORKERN_MEMORY_DIR; M=$KR/memory
mkdir -p "$M/dev$N" "$M/shared" "$M/global" "$HOME/dev/dev$N"
corkern add "dev$N" "$HOME/dev/dev$N" >> "$LOG" 2>&1   # every device edits the registry at once
START="{\"hook_event_name\":\"SessionStart\",\"cwd\":\"$HOME/dev/dev$N\"}"
for r in $(seq "$ROUNDS"); do
    echo "dev$N-r$r-unique" > "$M/dev$N/note-$r.md"
    record="dev$N-r$r-unique"
    if (( r % 3 == 0 )); then
        echo "- [dev$N r$r](x.md) dev$N-r$r-index" >> "$M/shared/MEMORY.md"; record="$record dev$N-r$r-index"
    fi
    if (( r % 4 == 0 )); then
        echo "dev$N-r$r-hot" > "$M/shared/hot.md"; record="$record dev$N-r$r-hot"
    fi
    if (( r % 5 == 0 )); then
        echo "dev$N-r$r-global" > "$M/global/dev$N-r$r.md"; record="$record dev$N-r$r-global"
    fi
    echo "$record" | tr ' ' '\n' >> "$SHARED/written-dev$N.txt"
    fire corkern-commit-memory                    # real background behaviour
    case $(( RANDOM % 4 )) in                       # many short sessions
        0) fire corkern-push ;;
        1) echo "$START" | bash "$HOOK" corkern-session-start > /dev/null ;;  # pull + context + metrics
    esac
    sleep "0.$(( RANDOM % 5 ))"
done

# Session end, then wait until every device has pushed
for _ in $(seq 100); do pgrep -f "corkern-(commit-memory|pull|push)" >/dev/null || break; sleep 0.2; done
fire corkern-push
touch "$FLAGS/pushed-$N"
while [ "$(count pushed)" -lt 5 ]; do sleep 0.3; done

# Final sync: push anything a late pull merged, then pull until converged with the remote
for _ in 1 2 3; do fire corkern-push; fire corkern-pull; done
for _ in $(seq 100); do pgrep -f "corkern-(commit-memory|pull|push)" >/dev/null || break; sleep 0.2; done
touch "$FLAGS/synced-$N"
while [ "$(count synced)" -lt 5 ]; do sleep 0.3; done
fire corkern-pull
{
    echo "tree $(git -C "$KR" rev-parse 'HEAD^{tree}')"
    echo "status $(git -C "$KR" status --porcelain | wc -l)"
    [ -d "$KR/.git/rebase-merge" ] || [ -d "$KR/.git/rebase-apply" ] || [ -f "$KR/.git/MERGE_HEAD" ] && echo "stuck yes"
    for f in "$KR"/.last-sync-*-error; do [ -f "$f" ] && echo "error $(basename "$f"): $(grep reason "$f")"; done
    [ -f "$KR/.last-sync-conflicts" ] && echo "conflicts $(wc -l < "$KR/.last-sync-conflicts")"
} > "$SHARED/result-dev$N.txt"
touch "$FLAGS/done-$N"
wait_for "$FLAGS/verified"   # stay up until the judge is done (compose aborts on first exit)
