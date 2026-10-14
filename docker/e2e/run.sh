#!/usr/bin/env bash
# E2E orchestrator (runs as root in the container): build the code repo, give two users
# a realistic v1 install each, then run the device scenarios and the pytest suite as
# those non-root users. Exits non-zero if any phase fails.
set -uo pipefail
E2E=/src/docker/e2e
status=0

echo "══ setup ══"
git config --system safe.directory '*'
rm -rf /opt/code && cp -r /src /opt/code
git -C /opt/code init -q -b main
git -C /opt/code -c user.name=e2e -c user.email=e2e@x.invalid add -A
git -C /opt/code -c user.name=e2e -c user.email=e2e@x.invalid commit -q -m "code under test"
chmod -R a+rX /opt/code
mkdir -p /opt/shared/proj /srv/shared && chown alice /srv/shared

for u in alice bob; do
    runuser -u "$u" -- git config --global user.name "$u"
    runuser -u "$u" -- git config --global user.email "$u@e2e.invalid"
    runuser -u "$u" -- env HOME="/home/$u" USER="$u" bash "$E2E/setup-v1.sh" || status=1
done

echo; echo "══ device 1: alice ══"
runuser -u alice -- env HOME=/home/alice USER=alice bash "$E2E/device1.sh" || status=1

echo; echo "══ device 2: bob ══"
runuser -u bob -- env HOME=/home/bob USER=bob bash "$E2E/device2.sh" || status=1

echo; echo "══ pytest as alice (real-v1 probes run against alice's fake v1) ══"
# shellcheck disable=SC2016  # expands inside the user's shell
runuser -u alice -- env HOME=/home/alice USER=alice bash -c '
    set -eo pipefail
    . /src/docker/e2e/lib.sh
    fingerprint > /tmp/v1-pre-pytest
    cp -r /src "$HOME/corkern-v2-src" && cd "$HOME/corkern-v2-src"
    uv venv -q --seed && uv pip install -q -e ".[dev]"
    .venv/bin/pytest tests/ -q -rs -p no:cacheprovider | grep -E "FAILED|ERROR|passed|failed" | tail -12
    fingerprint > /tmp/v1-post-pytest
    diff /tmp/v1-pre-pytest /tmp/v1-post-pytest && echo "  ✅ v1 byte-identical after full pytest run"
' || status=1

echo
[ "$status" -eq 0 ] && echo "══ E2E: ALL PHASES PASSED ══" || echo "══ E2E: FAILURES ══"
exit "$status"
