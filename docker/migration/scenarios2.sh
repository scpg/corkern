#!/usr/bin/env bash
# 2-setup-new-machine.sh against what can go wrong on the new machine: damaged or odd export
# folders, a fresh laptop (no git identity), a remote that is not empty, leftovers of an earlier
# try, a failure midway. Each scenario is a fresh user with its own empty remote. Judged by
# invariants:
#
#   every run     no Python traceback; no conflict markers in the knowledge repo
#   success       every FACT of the export is in the knowledge repo AND on the remote; odd files
#                 (CRLF, binary, unicode names) arrive byte-identical; the repo is pushed
#   refusal       says how to fix it; "nothing was changed" means: nothing installed, no
#                 knowledge repo, the remote exactly as before
set -uo pipefail
# shellcheck source=docker/e2e/lib.sh
. /src/docker/e2e/lib.sh
MIG=/opt/code/migrate
MARK='^(<<<<<<< |>>>>>>> )'
S=/srv/s2
RESULTS=()

section "setup: a v1 machine and its export"
git config --system safe.directory '*'
cp -r /src /opt/code && git -C /opt/code init -q -b main && git -C /opt/code add -A \
  && git -C /opt/code -c user.name=t -c user.email=t@t.invalid commit -q -m code && chmod -R a+rX /opt/code
mkdir -p "$S" && chmod 1777 "$S"
asu() { local u="$1"; shift; runuser -u "$u" -- env HOME="/home/$u" USER="$u" CORKERN_DEVICE_NAME="$u-pc" \
          PATH="/home/$u/.local/bin:/usr/local/bin:/usr/bin:/bin" "$@"; }
asu old git config --global user.name old; asu old git config --global user.email old@mig.invalid
asu old git init -q --bare -b main "$S/v1.git"
asu old bash -c "set -e; mkdir -p ~/dev/corkern && cd ~/dev/corkern && tar -xf /v1-code.tar \
  && mkdir -p memory/corkern memory/webshop global .devices \
  && printf 'corkern:dev/corkern\nwebshop:dev/projects/webshop\n' > projects.conf \
  && printf -- '- [State](state.md)\n' > memory/corkern/MEMORY.md \
  && echo FACT-state > memory/corkern/state.md && echo FACT-grep > memory/webshop/facts.md \
  && echo FACT-global > global/CLAUDE.md && echo FACT-uni > 'memory/webshop/ünïcødé.md' \
  && printf 'FACT-crlf\r\nsecond\r\n' > memory/webshop/crlf.md && head -c 4096 /dev/urandom > memory/webshop/blob.bin \
  && printf 'date\thost\tslug\n2026-01-01 10:00:00\told\tcorkern\n' > .metrics.tsv \
  && git init -q -b main && git add -A && git -c commit.gpgsign=false commit -qm base \
  && git remote add origin $S/v1.git && git push -q -u origin main"
check "seed: 1-export-v1.sh" asu old bash "$MIG/1-export-v1.sh" --yes
SEED=/home/old/corkern-migration
SEEDV1=/home/old/dev/corkern
[ "$FAIL" -gt 0 ] && { finish; exit 1; }

# world NAME — a fresh "new laptop" user with an empty remote and a copy of the export folder
N=0
world() {
    NAME="$1"; W="$S/$1"; N=$((N + 1)); U="nm$N"
    FACTS=(FACT-state FACT-grep FACT-global FACT-uni FACT-crlf); EXTRA=(memory/webshop/crlf.md memory/webshop/blob.bin "memory/webshop/ünïcødé.md")
    useradd -m -s /bin/bash "$U"; mkdir -p "$W"
    git init -q --bare -b main "$W/kr.git"
    B="/home/$U/corkern-migration"; cp -r "$SEED" "$B"
    chown -R "$U:$U" "$W" "$B"
    asu "$U" git config --global user.name "$U"; asu "$U" git config --global user.email "$U@mig.invalid"
    KR="/home/$U/.corkern"; REMOTE="$W/kr.git"; RUN_ENV=()
}
remote_state() { git -C "$W/kr.git" for-each-ref 2>/dev/null | sha256sum; }
snap() { REMOTE_BEFORE=$(remote_state); }
run2() { asu "$U" env "${RUN_ENV[@]}" bash "$B/2-setup-new-machine.sh" --remote "$REMOTE" --code-repo /opt/code --yes > "$W/output" 2>&1; RC=$?; }
# rewrite the export (a member of the tar.gz) and its checksum, as if a different tool made it
reexport() {   # reexport 'shell command run inside the unpacked export'
    local t="$W/re"; rm -rf "$t"; mkdir -p "$t"; tar -xzf "$B/v1-export.tar" -C "$t"
    (cd "$t" && eval "$1") && (cd "$t" && tar -czf "$B/v1-export.tar" ./*) && (cd "$B" && sha256sum v1-export.tar > v1-export.tar.sha256)
    chown -R "$U:$U" "$B"
}

fail() { bad "$NAME: $1"; FAILED_HERE=1; }
verdict() {   # verdict ok|refuse "what" [pattern]
    local want="$1" desc="$2" pat="${3:-}" got=ok f
    FAILED_HERE=0
    [ "$RC" -ne 0 ] && got=refuse
    grep -q '^Traceback (most recent call last)' "$W/output" && fail "Python traceback"
    [ "$want" != "$got" ] && fail "expected $want, got $got"
    [ -n "$pat" ] && ! grep -qiE -- "$pat" "$W/output" && fail "output lacks /$pat/"
    [ -d "$KR" ] && grep -rlE --exclude-dir=.git "$MARK" "$KR" >/dev/null 2>&1 && fail "conflict markers in the knowledge repo"
    if [ "$got" = ok ]; then
        git -C "$W/kr.git" log --all -p --binary > "$W/pushed" 2>&1
        for f in "${FACTS[@]}"; do
            grep -rqF --exclude-dir=.git -- "$f" "$KR" || fail "knowledge repo lacks $f"
            grep -qF -- "$f" "$W/pushed" || fail "remote lacks $f"
        done
        for f in "${EXTRA[@]}"; do cmp -s "$SEEDV1/$f" "$KR/$f" || fail "not byte-identical: $f"; done
        [ "$(asu "$U" git -C "$KR" rev-parse HEAD)" = "$(git -C "$W/kr.git" rev-parse HEAD 2>/dev/null)" ] || fail "not pushed"
    else
        grep -qE 'fix:|resumes here' "$W/output" || fail "refused without saying how to fix it"
        if grep -q 'nothing was changed' "$W/output"; then
            [ ! -e "/home/$U/.local/bin/corkern" ] || fail "corkern installed although nothing was supposed to change"
            [ "$(remote_state)" = "$REMOTE_BEFORE" ] || fail "the remote changed although nothing was supposed to"
        fi
    fi
    if [ "$FAILED_HERE" = 0 ]; then ok "$NAME: $desc → $got"
    else sed 's/^/     │ /' "$W/output" | grep -vE '^\s+│\s*$' | tail -14; fi
    RESULTS+=("$(printf '%-22s %-7s %-7s %s' "$NAME" "$want" "$got" "$([ "$FAILED_HERE" = 0 ] && echo ✅ || echo ❌)")")
}

section "the new machine"
world clean;        snap; run2; verdict ok "export copied as it is"
world moved;        mv "$B" "/home/$U/Downloads-corkern"; B="/home/$U/Downloads-corkern"; snap; run2; verdict ok "export folder renamed/moved"
world rerun;        run2; snap; run2; verdict ok "second run after success" "\(done "
world damaged;      printf 'x' | dd of="$B/v1-export.tar" bs=1 seek=100 conv=notrunc 2>/dev/null; snap; run2; verdict refuse "export damaged on the way" "intact"
world incomplete;   rm "$B/manifest.txt"; snap; run2; verdict refuse "folder copied incompletely" "export folder"
world newer-format; reexport "sed -i 's/\"format_version\": 1/\"format_version\": 2/' manifest.json"; snap; run2
                    verdict refuse "export from a newer corkern" "newer|update"
world markers;      reexport "printf '<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n' >> data/memory/corkern/state.md \
                    && python3 /src/docker/migration/remanifest.py"; FACTS=(); EXTRA=(); snap; run2
                    verdict refuse "export carries conflict markers (made by an older script)" "no conflict markers"
assert "markers: nothing reached the remote" bash -c "[ -z \"\$(git -C '$W/kr.git' for-each-ref)\" ]"
world secret;       reexport "printf 'aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n' > data/memory/webshop/creds.md \
                    && python3 /src/docker/migration/remanifest.py"; snap; run2; verdict ok "a secret in the v1 data is redacted before the migration commit" "secret"
assert "secret: never reaches the remote (the original stays in the quarantine)" bash -c "! git -C '$W/kr.git' log --all -p | grep -qF wJalrXUtnFEMI && grep -rqF wJalrXUtnFEMI /home/$U/.local/state/corkern/quarantine"
world remote-used;  git clone -q "$W/kr.git" "$W/x" 2>/dev/null; (cd "$W/x" && echo other > f && git add f && git -c user.name=x -c user.email=x@x commit -qm other && git push -q origin HEAD:main)
                    chown -R "$U:$U" "$W"; snap; run2; verdict refuse "the remote already has content" "empty"
world unreachable;  REMOTE=/nonexistent/kr.git; snap; run2; verdict refuse "remote not reachable" "reachable"
world kr-exists;    asu "$U" mkdir -p "$KR"; snap; run2; verdict refuse "a knowledge repo is already there" "knowledge repo"
world v1-here;      asu "$U" mkdir -p "/home/$U/dev/corkern"; asu "$U" touch "/home/$U/dev/corkern/projects.conf"; snap; run2
                    verdict refuse "corkern v1 is installed here" "v1"
world fresh-laptop; asu "$U" rm -f "/home/$U/.gitconfig"; snap; run2
                    verdict refuse "no git identity yet (fresh laptop)" "user.(name|email)"
world push-fails;   chmod -R a-w "$W/kr.git"; snap; run2; verdict refuse "remote rejects the push (midway)" "resumes here"
                    chmod -R u+w "$W/kr.git"; run2; verdict ok "…rerun resumes and finishes" "\(done "

section "summary"
printf '  %-22s %-7s %-7s\n' scenario wanted got
printf '  %s\n' "${RESULTS[@]}"
finish
