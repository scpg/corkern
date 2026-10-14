#!/usr/bin/env bash
# Build a realistic fake v1 corkern install for the current user, modelled on a real one:
# ~/dev/corkern (git repo: code + memory + projects.conf), v1 `corkern` command,
# ~/.claude settings + memory symlinks into v1, sentinel, uncommitted v1 changes.
set -euo pipefail
V1="$HOME/dev/corkern"
mkdir -p "$V1"/{memory/{corkern/sessions,webshop,global,home},.devices,global,hooks} "$HOME/.local/bin"

cat > "$V1/projects.conf" <<'EOF'
# slug:path
#
# path  = relative to HOME -OR- absolute
#           Relative:          dev/projects/webshop
#           Windows absolute:  C:/Users/user/Documents/webshop
#
corkern:dev/corkern
webshop:dev/projects/webshop
toolbox:dev/projects/toolbox
home:
shared:/opt/shared/proj
winonly:C:/Users/someone/proj
dup:dev/dup-first
dup:dev/dup-second
missing:dev/not-here
EOF
printf -- '---\nname: state\n---\nv1 state for %s\n' "$USER" > "$V1/memory/corkern/project_state.md"
echo "- [State](project_state.md)" > "$V1/memory/corkern/MEMORY.md"
echo "- session note" > "$V1/memory/corkern/sessions/2026-06-24-0558.md"
echo "- [ ] reminder" > "$V1/memory/corkern/todo.md"
echo "webshop memory" > "$V1/memory/webshop/notes.md"
printf '\x89PNG\r\n\x1a\n\x00\x01\x02binary' > "$V1/memory/webshop/diagram.png"
echo "principles" > "$V1/memory/global/principles.md"
echo "home memory" > "$V1/memory/home/x.md"
printf 'host:host-b\ncorkern:2026-06-22 22:28:12:/home/userb/dev/corkern\n' > "$V1/.devices/host-b"
printf 'host:%s\n' "$(hostname)" > "$V1/.devices/host-a"
echo "# my global instructions" > "$V1/global/CLAUDE.md"
# shellcheck disable=SC2016  # literal script text
printf '#!/bin/bash\ngit -C "$CORKERN" push\n' > "$V1/hooks/corkern-push.sh"
printf '#!/bin/bash\necho "corkern v1 $*"\n' > "$V1/corkern"
echo "# corkern v1" > "$V1/README.md"
chmod +x "$V1/corkern" "$V1/hooks/corkern-push.sh"

git -C "$V1" init -q -b main
git -C "$V1" add -A
git -C "$V1" commit -q -m "v1 state"

# Uncommitted v1 work — must NOT appear in a git-archive migration
echo "newproj:dev/newproj" >> "$V1/projects.conf"
echo "uncommitted" > "$V1/memory/webshop/uncommitted.md"
touch "$V1/.corkern-v1.sentinel"

# v1 runtime wiring
ln -s "$V1/corkern" "$HOME/.local/bin/corkern"   # like v1 sync.sh: a link into the repo
mkdir -p "$HOME/.claude/projects/-home-$USER-dev-projects-webshop" "$HOME/.claude/projects/-home-$USER-dev-corkern"
ln -s "$V1/memory/webshop" "$HOME/.claude/projects/-home-$USER-dev-projects-webshop/memory"
ln -s "$V1/memory/corkern" "$HOME/.claude/projects/-home-$USER-dev-corkern/memory"
ln -s "$V1/global/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
cat > "$HOME/.claude/settings.json" <<EOF
{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "$V1/hooks/corkern-pull.sh"}]}]}}
EOF

# Project directories that exist on this device
mkdir -p "$HOME/dev/projects/webshop" "$HOME/dev/dup-first"
[ "$USER" = alice ] && mkdir -p "$HOME/dev/projects/toolbox"
echo "fake v1 ready for $USER"
