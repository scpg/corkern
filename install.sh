#!/usr/bin/env bash
# corkern v2 installer — installs the `corkern` CLI and sets up a knowledge repo.
#
# Safety (v1 coexistence):
#   - refuses any target inside the v1 repo (same rules as src/corkern/guard.py),
#     before anything is installed; corkern then re-checks every path itself
#   - installs only `corkern`; aborts if a `corkern` command appears or changes
#   - never touches ~/.claude, ~/dev/corkern, or shell rc files — it prints the
#     lines to add instead. Hook wiring (init-device) is not done until v2 UAT.
set -euo pipefail

CODE_REPO="${CORKERN_CODE_REPO:-git@github.com:scpg/corkern.git}"
CODE_REF="${CORKERN_CODE_REF:-main}"
CODE_DIR="${CORKERN_CODE_DIR:-$HOME/.local/share/corkern}"
DEFAULT_KR="$HOME/.corkern"

usage() {
    cat <<EOF
Usage: install.sh [MODE] [--memory-dir PATH]

Knowledge repo (pick one; default: use an existing one):
  (none)                  use \$CORKERN_MEMORY_DIR or $DEFAULT_KR if it exists
  --new                   create a new local knowledge repo
  --clone URL             clone an existing knowledge repo
  --create-github NAME    create a new knowledge repo and a PRIVATE GitHub repo (gh)
  --migrate BUNDLE        build the knowledge repo from a v1 export, made on the v1 machine with:
                            git -C ~/dev/corkern archive HEAD memory .devices global projects.conf > v1-export.tar
                          (commit v1 first: only committed data is in the export)
  --memory-dir PATH       where the knowledge repo is / goes (default: $DEFAULT_KR)

Environment: CORKERN_CODE_REPO, CORKERN_CODE_REF, CORKERN_CODE_DIR
EOF
}

die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }
warn() { echo "WARNING: $*" >&2; }

# ── v1 guard (mirrors guard.py) ──────────────────────────────────────────────
real_home() { getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6; }

assert_not_v1() {
    local given="$1" form root c
    local -a homes=("$HOME") roots=()
    local rh; rh=$(real_home) && [ -n "$rh" ] && homes+=("$rh")
    for h in "${homes[@]}"; do roots+=("$h/dev/corkern" "$h/.claude")
        [ -L "$h/.local/bin/corkern" ] && [ -e "$(dirname "$(realpath -- "$h/.local/bin/corkern")")/projects.conf" ] \
            && roots+=("$h/.local/bin/corkern"); done
    [ -n "${CORKERN:-}" ] && roots+=("$CORKERN")

    for form in "$(realpath -m -s -- "$given")" "$(realpath -m -- "$given")"; do
        for root in "${roots[@]}"; do
            for r in "$(realpath -m -s -- "$root")" "$(realpath -m -- "$root")"; do
                if [ "$form" = "$r" ] || [[ "$form" == "$r"/* ]]; then
                    die "$given is v1-protected ($r is a known v1 location)."
                fi
            done
        done
        c="$form"
        while :; do
            [ -e "$c/.corkern-v1.sentinel" ] && die "$given is v1-protected ($c contains .corkern-v1.sentinel)."
            [ -e "$c/projects.conf" ] && die "$given is v1-protected ($c contains projects.conf, v1 layout)."
            [ "$c" = "/" ] && break
            c=$(dirname "$c")
        done
    done
}


# ── arguments ────────────────────────────────────────────────────────────────
MODE="" ARG="" KR=""
set_mode() { [ -z "$MODE" ] || die "choose only one of --new, --clone, --create-github, --migrate."; MODE="$1"; ARG="${2:-}"; }
while [ $# -gt 0 ]; do
    case "$1" in
        --new)           set_mode new ;;
        --clone)         [ $# -ge 2 ] || die "--clone needs a URL"; set_mode clone "$2"; shift ;;
        --create-github) [ $# -ge 2 ] || die "--create-github needs a name"; set_mode github "$2"; shift ;;
        --migrate)       [ $# -ge 2 ] || die "--migrate needs the v1 export file"; set_mode migrate "$(realpath -m -- "$2")"; shift ;;
        --memory-dir)    [ $# -ge 2 ] || die "--memory-dir needs a path"; KR="$2"; shift ;;
        -h|--help)       usage; exit 0 ;;
        *)               usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

KR="${KR:-${CORKERN_MEMORY_DIR:-$DEFAULT_KR}}"
KR="$(realpath -m -- "$KR")"

# ── validate everything before installing anything ───────────────────────────
assert_not_v1 "$KR"
assert_not_v1 "$CODE_DIR"

if [ -z "$MODE" ]; then
    if [ ! -e "$KR" ]; then
        usage >&2
        die "no knowledge repo at $KR. Use --new, --clone URL, --create-github NAME, or --migrate V1-EXPORT."
    fi
    MODE=existing
fi
case "$MODE" in
    existing) [ -f "$KR/corkern.conf" ] || die "$KR is not a corkern knowledge repo (no corkern.conf)." ;;
    *)        if [ -e "$KR" ] && [ -n "$(ls -A -- "$KR")" ]; then die "$KR exists and is not empty."; fi ;;
esac

[ "$MODE" = migrate ] && { [ -f "$ARG" ] || die "v1 export not found: $ARG"; }


# ── prerequisites ────────────────────────────────────────────────────────────
command -v git >/dev/null     || die "git is required."
command -v uv >/dev/null      || die "uv is required: https://docs.astral.sh/uv/"
command -v python3 >/dev/null || die "python3 >= 3.10 is required."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "python3 >= 3.10 is required."
[ "$MODE" = github ] && { command -v gh >/dev/null || die "gh (GitHub CLI) is required for --create-github."; }
command -v claude >/dev/null || warn "claude CLI not found — needed later for hook wiring."
# v1's `corkern` is a link into its repo — v2 installs its own `corkern` into the same folder
BIN="$(uv tool dir --bin)"
if [ -L "$BIN/corkern" ] && [ -e "$(dirname "$(realpath -- "$BIN/corkern")")/projects.conf" ]; then
    die "corkern v1 is installed here ($BIN/corkern → its repo). Switch this machine with
  migrate/3-switch-v1-machine.sh — it retires v1 (reversibly) and installs v2."
fi

# ── code + CLI ───────────────────────────────────────────────────────────────
# The code is fetched into a temporary checkout and installed as a uv tool; the hooks come
# from the installed package (corkern install-hooks) into $CODE_DIR/hooks. An older install
# kept a git checkout in $CODE_DIR itself — it is moved aside, never deleted.
if [ -d "$CODE_DIR/.git" ]; then
    old="$CODE_DIR.old-clone-$(date +%Y%m%d-%H%M%S)"
    info "Older git-clone install found — moving it aside: $old"
    mv -- "$CODE_DIR" "$old"
elif [ -e "$CODE_DIR" ] && [ ! -d "$CODE_DIR/hooks" ]; then
    die "$CODE_DIR exists but is not a corkern hooks folder."
fi
SRC_TMP="$(mktemp -d)"
trap 'rm -rf "$SRC_TMP"' EXIT
info "Fetching $CODE_REPO ($CODE_REF)"
git clone -q --depth 1 --branch "$CODE_REF" -- "$CODE_REPO" "$SRC_TMP/corkern"

info "Installing corkern with uv"
uv tool install --quiet --force "$SRC_TMP/corkern"
[ -x "$BIN/corkern" ] || die "corkern was not installed into $BIN."
# an older install.sh wrote $BIN/corkern2 as a wrapper (before the rename) — ours, now unused
grep -qs "CORKERN_MACHINE_REPO=" "$BIN/corkern2" && rm -f -- "$BIN/corkern2" && info "Removed the old corkern2 wrapper"
CORKERN2="$(uv tool dir)/corkern/bin/corkern"
[ -x "$CORKERN2" ] || die "corkern not found in uv's tool folder ($CORKERN2)."
# An older install.sh replaced $BIN/corkern by a wrapper holding this machine's repo; uv's
# install above wrote the real command again, and `corkern setup` records the repo in
# machine.conf (~/.config/corkern/) — nothing machine-specific in ~/.claude or the command.
SETUP=()
case "$MODE" in
    existing) ;;
    new)      SETUP=(--new) ;;
    clone)    SETUP=(--clone "$ARG") ;;
    github)   SETUP=(--create-github "$ARG") ;;
    migrate)  SETUP=(--migrate "$ARG") ;;
esac
CORKERN_CODE_DIR="$CODE_DIR" "$CORKERN2" setup "${SETUP[@]}" --memory-dir "$KR" || die "corkern setup failed (see above)."
if [ "$MODE" = migrate ]; then
    echo
    info "To use it on your other machines, publish it as a PRIVATE repo, e.g.:"
    echo "    gh repo create <name> --private --source $KR --remote origin --push"
    echo "  then on each other machine:  install.sh --clone git@github.com:<you>/<name>.git"
fi
"$CORKERN2" status || true

# ── commit signing (per machine) — always the user's decision ───────────────
if [ -t 0 ] && [ -t 1 ]; then
    echo
    info "Commit signing for the knowledge repo (hooks commit in the background)"
    "$CORKERN2" signing || true      # asks before doing anything
else
    echo
    info "Commit signing not set up (no terminal to ask you). When ready, run:"
    echo "    corkern signing"
fi

# ── next steps (printed, never written) ──────────────────────────────────────
echo
info "Done. corkern on this machine uses $KR (recorded in ~/.config/corkern/machine.conf)."
case ":$PATH:" in *":$BIN:"*) ;; *) info "Add to your shell profile:"; echo "    export PATH=\"$BIN:\$PATH\"" ;; esac
echo "Hook wiring (init-device) is not part of this installer until v2 UAT."
