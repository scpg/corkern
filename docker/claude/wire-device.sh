#!/usr/bin/env bash
# Sets one test user's device up the way a user would (runs AS that user):
#   corkern add demo + corkern init-device --apply → autoMemoryDirectory for the project,
#   hooks in ~/.claude/settings.json (machine-neutral), global CLAUDE.md import.
# Test-only extras: a hook trace (CORKERN_HOOK_LOG, added next to corkern's settings — which
# also checks that init-device keeps settings it did not write) and skipped onboarding.
set -euo pipefail
export CORKERN_MEMORY_DIR="$HOME/.corkern"
PROJECT="$HOME/dev/projects/demo"
mkdir -p "$PROJECT" "$HOME/.claude"
git -C "$PROJECT" init -q
echo '{"env": {"CORKERN_HOOK_LOG": "'"$HOME"'/hook-trace.log"}, "model": "haiku"}' > "$HOME/.claude/settings.json"
corkern add demo "$PROJECT" >/dev/null
corkern init-device --apply --yes > "$HOME/init-device.txt"
echo '{"hasCompletedOnboarding": true}' > "$HOME/.claude.json"
mkdir -p "$CORKERN_MEMORY_DIR/memory/demo"
echo "$CORKERN_MEMORY_DIR/memory/demo"
