#!/usr/bin/env bash
# Two fresh devices (alice, bob) with real Claude Code + corkern v2 hooks, syncing through
# a shared bare remote.
#   Layer 1 (always): fire each hook exactly as wired in ~/.claude/settings.json, with
#                     Claude-Code-shaped JSON on stdin.
#   Layer 2 (only if CLAUDE_CODE_OAUTH_TOKEN is set): real headless Claude sessions —
#                     alice writes a memory, bob's next session must see it.
set -uo pipefail
# shellcheck source=docker/e2e/lib.sh
. /src/docker/e2e/lib.sh
export CORKERN_CODE_REPO=/opt/code CORKERN_CODE_REF=main
REMOTE=/srv/shared/kr.git
as() { local u="$1"; shift; runuser -u "$u" -- env HOME="/home/$u" USER="$u" CORKERN_DEVICE_NAME="$u-pc" \
         PATH="/home/$u/.local/bin:/usr/local/bin:/usr/bin:/bin" "$@"; }
c2() { local u="$1"; shift; as "$u" env CORKERN_MEMORY_DIR="/home/$u/.corkern" corkern "$@"; }

section "setup"
git config --system safe.directory '*'
cp -r /src /opt/code && git -C /opt/code init -q -b main && git -C /opt/code add -A \
  && git -C /opt/code -c user.name=t -c user.email=t@t.invalid commit -q -m code && chmod -R a+rX /opt/code
mkdir -p /srv/shared && git init -q --bare --shared=0777 -b main "$REMOTE" && chmod -R a+rwX /srv/shared
for u in alice bob; do
    as "$u" git config --global user.name "$u"; as "$u" git config --global user.email "$u@e2e.invalid"
done
expect "real Claude Code installed" "2.1.285" as alice claude --version
check  "alice: install --new"         as alice bash /opt/code/install.sh --new
as alice bash -c "mkdir -p ~/.corkern/global && printf '%s\n' '# Global instructions (corkern)' \
    '- The team mascot is a pelican named Ottokar.' > ~/.corkern/global/CLAUDE.md \
    && git -C ~/.corkern add -A && git -C ~/.corkern commit -qm 'global instructions'"
check  "alice: publish knowledge repo" as alice bash -c "git -C ~/.corkern remote add origin $REMOTE && git -C ~/.corkern push -q -u origin main"
check  "bob: install --clone"         as bob bash /opt/code/install.sh --clone "$REMOTE"
MEM_A=$(as alice bash /src/docker/claude/wire-device.sh)
MEM_B=$(as bob bash /src/docker/claude/wire-device.sh)

section "init-device wrote Claude Code's settings (no symlinks, no hand-made JSON)"
expect "project memory → knowledge repo (autoMemoryDirectory)" "/home/alice/.corkern/memory/demo" \
    as alice jq -r .autoMemoryDirectory /home/alice/dev/projects/demo/.claude/settings.local.json
for ev in SessionStart PostToolUse Stop SessionEnd; do
    expect "settings.json: $ev hook" "corkern.sh" as alice jq -r ".hooks.${ev}[0].hooks[0].command" /home/alice/.claude/settings.json
done
expect "settings.json: no machine path in env" "null" as alice jq -r .env.CORKERN_MEMORY_DIR /home/alice/.claude/settings.json
assert "settings.json: corkern's hooks are machine-neutral (no /home/alice)" bash -c "! jq -c .hooks /home/alice/.claude/settings.json | grep -q /home/alice"
expect "settings.json: other settings kept" "haiku" as alice jq -r .model /home/alice/.claude/settings.json
expect "settings.json: other env kept" "hook-trace.log" as alice jq -r .env.CORKERN_HOOK_LOG /home/alice/.claude/settings.json
expect "CLAUDE.md imports the global instructions" "@~/.corkern/global/CLAUDE.md" cat /home/alice/.claude/CLAUDE.md
assert "previous settings.json kept as backup" bash -c "ls /home/alice/.claude/settings.json.bak-corkern-* >/dev/null"
assert "project repo stays clean (settings excluded from its git)" \
    test -z "$(as alice git -C /home/alice/dev/projects/demo status --porcelain)"
expect "status: hooks wired" "Hooks wired in Claude Code" c2 alice status
expect "init-device again: nothing to change" "Nothing to change" c2 alice init-device
expect "slash commands installed" "corkern harvest" cat /home/alice/.claude/commands/corkern-harvest.md
expect "status -v: project wiring"  "✓ demo" c2 alice status -v
expect "status -v: slash commands"  "slash commands: /corkern-distill" c2 alice status -v

section "layer 1: hooks fired exactly as wired in settings.json"
# fire USER EVENT JSON — run the command(s) Claude Code would run for EVENT
fire() {
    local u="$1" ev="$2" json="$3" cmd
    # shellcheck disable=SC2016  # $e is a jq variable
    cmd=$(as "$u" jq -r --arg e "$ev" '.hooks[$e][0].hooks[0].command' "/home/$u/.claude/settings.json")
    # no CORKERN_MEMORY_DIR: like Claude Code, the hook gets nothing but settings.json
    as "$u" env -u CORKERN_MEMORY_DIR CORKERN_HOOK_FOREGROUND=1 \
        bash -c "cd ~/dev/projects/demo && $cmd" <<< "$json"
}
POST='{"hook_event_name":"PostToolUse","tool_name":"Write","tool_input":{"file_path":"x"}}'
as alice bash -c "echo 'layer1 from alice' > '$MEM_A/layer1.md'"
check  "PostToolUse → commit-memory commits"   fire alice PostToolUse "$POST"
expect "commit message names the file"         "memory: demo/layer1.md" as alice git -C /home/alice/.corkern log -1 --format=%s
check  "Stop → push reaches the remote"        fire alice Stop '{"hook_event_name":"Stop"}'
expect "remote has alice's memory"             "memory/demo/layer1.md" git -C "$REMOTE" ls-tree -r --name-only main
check  "SessionStart → pull on bob"            fire bob SessionStart '{"hook_event_name":"SessionStart","source":"startup"}'
expect "bob sees alice's memory"               "layer1 from alice" cat "$MEM_B/layer1.md"
expect "SessionStart gives Claude the corkern context" "# Corkern — AI Instructions" \
    fire bob SessionStart '{"hook_event_name":"SessionStart","cwd":"/home/bob/dev/projects/demo"}'
assert "PostToolUse/Stop print nothing (no context pollution)" \
    test -z "$(fire bob PostToolUse "$POST")$(fire bob Stop '{}')"

section "init-device: Claude Code memory via autoMemoryDirectory (no symlink)"
AUTO=/home/alice/dev/projects/auto
as alice mkdir -p "$AUTO"
check  "alice: register project 'auto'"   as alice env CORKERN_MEMORY_DIR=/home/alice/.corkern corkern add auto "$AUTO"
expect "plan shows the setting, changes nothing" "autoMemoryDirectory = /home/alice/.corkern/memory/auto" \
    as alice env CORKERN_MEMORY_DIR=/home/alice/.corkern corkern init-device
assert "plan wrote nothing" test ! -e "$AUTO/.claude/settings.local.json"
check  "init-device --apply (no v1 on this machine)" \
    as alice env CORKERN_MEMORY_DIR=/home/alice/.corkern corkern init-device --apply --yes
expect "settings.local.json points at the knowledge repo" "/home/alice/.corkern/memory/auto" \
    as alice jq -r .autoMemoryDirectory "$AUTO/.claude/settings.local.json"
as alice bash -c "mkdir -p ~/.corkern/memory/auto && printf '%s\n' '# Memory index' \
    '- The project codename is PELICAN-42 (decided 2026-10-02)' > ~/.corkern/memory/auto/MEMORY.md"

section "list: every project on every device"
fire bob SessionStart '{"hook_event_name":"SessionStart","cwd":"/home/bob/dev/projects/demo"}' >/dev/null
fire bob Stop '{"hook_event_name":"Stop"}'
fire alice SessionStart '{"hook_event_name":"SessionStart","cwd":"/home/alice/dev/projects/demo"}' >/dev/null
expect "alice sees her own row: synced"  "demo .*alice-pc (local) .*synced" c2 alice list
expect "alice sees bob's device"         "demo .*bob-pc .*/home/bob/dev/projects/demo .*registered" c2 alice list
expect "-c / -s work like v1"            "bob-pc .*demo" c2 alice list -c DEVICE,SLUG -s DEVICE

section "IWE knowledge graph as an MCP server (real iwe 0.24.2)"
expect "iwe found"                        "✓ iwe " c2 alice iwe
check  "iwe --setup (config in the knowledge repo)" c2 alice iwe --setup
check  "iwe --install (MCP server in Claude Code)"  c2 alice iwe --install --yes
expect "Claude Code connects to it"       "iwe: .*corkern iwe-serve.*Connected" as alice bash -c "cd ~/dev/projects/demo && claude mcp list"
as alice bash -c "printf '%s\n' '# Pelican field notes' '' 'Pelicans scoop fish with a throat pouch that holds about eleven litres.' \
    > ~/.corkern/memory/demo/pelican-field-notes.md"
expect "the graph sees the knowledge repo" "pelican-field-notes" as alice bash -c "cd ~/.corkern && iwe find --fuzzy pelican"
fire alice Stop '{"hook_event_name":"Stop"}'
fire bob SessionStart '{"hook_event_name":"SessionStart","cwd":"/home/bob/dev/projects/demo"}' >/dev/null
expect "bob gets the IWE config through sync" "✓ .iwe/config.toml" c2 bob iwe

section "layer 2: real Claude sessions"
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    echo "  ⏭  skipped — no token passed (run: make test-claude)"
else
    ALICE_PROMPT="Use the Write tool to create the file $MEM_A/note_from_alice.md with exactly this content: hello from alice's claude. Do nothing else and reply DONE."
    check "alice: Claude session writes a memory" as alice timeout 300 bash -c \
        "cd ~/dev/projects/demo && claude -p \"\$1\" --model haiku --dangerously-skip-permissions" _ "$ALICE_PROMPT"
    expect "PostToolUse hook committed it"  "note_from_alice.md" as alice git -C /home/alice/.corkern log --format=%s -5
    for _ in $(seq 30); do git -C "$REMOTE" ls-tree -r --name-only main | grep -q note_from_alice && break; sleep 2; done
    expect "Stop hook pushed it"            "memory/demo/note_from_alice.md" git -C "$REMOTE" ls-tree -r --name-only main
    check "alice adds a global reminder" as alice env CORKERN_MEMORY_DIR=/home/alice/.corkern \
        corkern remind "water the plants at noon"
    check "alice's reminder is pushed" fire alice Stop '{"hook_event_name":"Stop"}'
    BOB_PROMPT="Do two things. 1) Read the file $MEM_B/note_from_alice.md with the Read tool. 2) Without using any tool, find the first open reminder in the 'Corkern: open reminders [global]' box of your session context. Reply with exactly two lines: the file content, then the reminder text."
    as bob timeout 300 bash -c "cd ~/dev/projects/demo && claude -p \"\$1\" --model haiku --dangerously-skip-permissions" \
        _ "$BOB_PROMPT" > /tmp/bob-answer.txt 2>&1
    expect "bob: next Claude session sees alice's memory"   "hello from alice's claude" cat /tmp/bob-answer.txt
    expect "bob: Claude got alice's reminder from SessionStart" "water the plants at noon" cat /tmp/bob-answer.txt
    as alice timeout 300 bash -c "cd $AUTO && claude -p \"\$1\" --model haiku --dangerously-skip-permissions" \
        _ "What is the project codename recorded in your auto memory (MEMORY.md)? Reply with only the codename." \
        > /tmp/auto-answer.txt 2>&1
    expect "Claude loads memory from autoMemoryDirectory (knowledge repo)" "PELICAN-42" cat /tmp/auto-answer.txt
    assert "no path-derived memory folder was needed" \
        bash -c "! ls -d /home/alice/.claude/projects/*auto*/memory 2>/dev/null | grep -q ."
    # v1's project "home" = ~: wired through Claude Code's own ~/.claude/settings.local.json,
    # which Claude Code reads only for sessions started in ~
    c2 alice add home /home/alice >/dev/null
    c2 alice init-device --apply --yes > /tmp/home-init.txt 2>&1
    as alice bash -c "mkdir -p ~/.corkern/memory/home && echo '- Codename: HOME-77' > ~/.corkern/memory/home/MEMORY.md"
    HOME_Q="What is the codename recorded in your auto memory (MEMORY.md)? Reply with only the codename, or NONE."
    as alice timeout 300 bash -c "cd ~ && claude -p \"\$1\" --model haiku --dangerously-skip-permissions" _ "$HOME_Q" > /tmp/home-answer.txt 2>&1
    expect "home project (~): Claude loads memory from the knowledge repo" "HOME-77" cat /tmp/home-answer.txt
    as alice timeout 300 bash -c "cd ~/dev/projects/demo && claude -p \"\$1\" --model haiku --dangerously-skip-permissions" _ "$HOME_Q" > /tmp/demo-answer.txt 2>&1
    assert "…only in ~: another project does not get the home memory" bash -c "! grep -q HOME-77 /tmp/demo-answer.txt"
    as alice timeout 300 bash -c "cd ~/dev/projects/demo && claude -p \"\$1\" --model haiku --dangerously-skip-permissions" \
        _ "Without using any tools: what is the name of the team mascot according to your global instructions? Reply with only the name." \
        > /tmp/mascot.txt 2>&1
    expect "Claude reads the global CLAUDE.md through the import line" "Ottokar" cat /tmp/mascot.txt
    as alice timeout 300 bash -c "cd ~/dev/projects/demo && claude -p \"\$1\" --model haiku --dangerously-skip-permissions" \
        _ "Use only the tools of the 'iwe' MCP server (not Read, Grep, Glob or Bash) to find the document about pelicans and tell me how many litres the throat pouch holds. Reply with only the number." \
        > /tmp/iwe-answer.txt 2>&1
    expect "Claude queries the knowledge repo through the IWE MCP server" "11\|[Ee]leven" cat /tmp/iwe-answer.txt
    as alice timeout 300 bash -c "cd ~/dev/projects/demo && claude -p \"\$1\" --model haiku" _ \
        "Context for this project, please acknowledge in one sentence: we decided to use Postgres instead of SQLite because several workers write at the same time, and the CI pipeline must stay under 10 minutes because the team deploys many times a day." \
        > /dev/null 2>&1 < /dev/null
    expect "prune: alice's real conversations are pending" "pending" c2 alice prune demo
    run_cmd as alice bash -c "cd ~/dev/projects/demo && echo all | CORKERN_MEMORY_DIR=~/.corkern corkern harvest demo --model haiku"
    if grep -qE "Wrote [0-9]+ memory file" "$OUT"; then ok "harvest with real Claude on real transcripts"
    else bad "harvest with real Claude on real transcripts (exit $RC)"; show; fi
    grep -E "Wrote|No memories|^      [a-z]" "$OUT" | head -8 | sed 's/^/     │ /'
    expect "prune: now harvested (manifest on alice-pc)" "harvested .*alice-pc" c2 alice prune demo
    expect "harvested memories are indexed in MEMORY.md" "[Pp]ostgres" cat /home/alice/.corkern/memory/demo/MEMORY.md
    as alice timeout 300 bash -c "cd ~/dev/projects/demo && claude -p '/corkern-status' --model haiku --dangerously-skip-permissions" \
        > /tmp/slash.txt 2>&1
    if grep -qiE "unknown (slash )?command" /tmp/slash.txt; then bad "/corkern-status slash command (unknown)"; sed 's/^/     │ /' /tmp/slash.txt | head -5
    else expect "/corkern-status slash command runs corkern status" "[Hh]ook\|[Ss]ync\|healthy\|warning\|good" cat /tmp/slash.txt; fi
    head -c 600 /tmp/slash.txt | sed 's/^/     │ /'; echo
    expect "alice: distill --auto with real Claude" "Saved: memory/global/synthesis.md" \
        as alice env CORKERN_MEMORY_DIR=/home/alice/.corkern timeout 300 corkern distill --auto --model haiku
    expect "synthesis has frontmatter + Claude's sections" "## " cat /home/alice/.corkern/memory/global/synthesis.md
    assert "no hook problems recorded (alice)" test ! -e /home/alice/.local/state/corkern/last-hook-problem
    assert "no sync errors (alice)" bash -c "! ls /home/alice/.corkern/.git/corkern/last-sync-*-error 2>/dev/null | grep -q ."
    assert "no sync errors (bob)" bash -c "! ls /home/bob/.corkern/.git/corkern/last-sync-*-error 2>/dev/null | grep -q ."
    assert "Claude-created MEMORY.md merged, no conflict copies" \
        bash -c "! find /home/bob/.corkern/memory -name '*.conflict-*' | grep -q ."
    if [ "$FAIL" -gt 0 ]; then
        section "diagnostics: hook trace (bob)"
        grep -iE "corkern-pull (start|end)|status: |error|fatal|overwritten|untracked|conflict|hint|would" /home/bob/hook-trace.log | head -40
        echo "alice commits:"; git -C /home/alice/.corkern log --format="  %s" -8
        echo "bob status:"; git -C /home/bob/.corkern status --porcelain
        echo "bob memory/demo:"; ls -la /home/bob/.corkern/memory/demo
        echo "bob state:"; find /home/bob/.corkern/.git/corkern -maxdepth 1 -name "last-sync*" -printf "  %f %TT\n"
        cat /home/bob/.local/state/corkern/last-hook-problem 2>/dev/null
    fi
fi

finish
