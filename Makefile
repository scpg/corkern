SHELL := /bin/bash

.PHONY: install-dev test test-docker test-e2e test-claude test-multi test-teams test-migration test-scenarios test-published

COMPOSE = docker compose -f docker/compose.test.yml

install-dev:
	uv venv --seed
	uv pip install -e ".[dev]"

test:
	.venv/bin/pytest tests/ -q

# Cleanup runs even when the tests fail; the test exit code is preserved
test-docker:
	$(COMPOSE) run --rm --build corkern-test; rc=$$?; $(COMPOSE) down -v; exit $$rc

test-e2e:
	$(COMPOSE) run --rm --build corkern-e2e; rc=$$?; $(COMPOSE) down -v; exit $$rc

# Real Claude Code in Docker. Layer 1 (hooks as wired) always; layer 2 (real headless
# sessions) only when ./.env provides CLAUDE_TOKEN_TESTING. The token is passed by name
# (`-e VAR`, value from the environment) — never on a command line, never in the image.
test-claude:
	@set -a; if [ -f .env ]; then . ./.env; fi; set +a; \
	CLAUDE_CODE_OAUTH_TOKEN="$${CLAUDE_TOKEN_TESTING:-}" \
	$(COMPOSE) run --rm --build -e CLAUDE_CODE_OAUTH_TOKEN corkern-claude; rc=$$?; \
	$(COMPOSE) down -v >/dev/null 2>&1; exit $$rc

# 5 device containers syncing through one remote, concurrently (ROUNDS=N to scale)
test-multi:
	docker compose -f docker/compose.multi.yml build -q; \
	docker compose -f docker/compose.multi.yml up --exit-code-from verify --no-log-prefix verify dev1 dev2 dev3 dev4 dev5 \
	  2>&1 | grep -vE '^(Attaching|Container|Network|Volume|Aborting)'; rc=$${PIPESTATUS[0]}; \
	docker compose -f docker/compose.multi.yml down -v >/dev/null 2>&1; exit $$rc

# 10 devices in 3 teams (+ an intruder): secrets, trust, index limit, init-device, no lost writes
TEAMS = docker compose -f docker/compose.teams.yml
TEAM_DEVICES = ana-laptop ana-desktop ben carla mallory dan eva finn gus-laptop gus-server
test-teams:
	$(TEAMS) build -q; \
	$(TEAMS) up --exit-code-from judge --no-log-prefix judge $(TEAM_DEVICES) \
	  2>&1 | grep -vE '^(Attaching|Container|Network|Volume|Aborting)'; rc=$${PIPESTATUS[0]}; \
	$(TEAMS) down -v >/dev/null 2>&1; exit $$rc

# Migration scripts end to end (LOCAL only — needs a git remote `v1` holding the v1 code): real v1 (v1/main) installed by its own sync.sh → export →
# new machine → switch → rollback. REAL_V1_EXPORT=path/to/v1-export.tar uses real data (local only).
V1_SRC ?= .
V1_REF ?= v1/main
test-migration:
	@mkdir -p tmp.local && git -C $(V1_SRC) archive $(V1_REF) -o $(CURDIR)/tmp.local/v1-code.tar
	docker build -q -f docker/Dockerfile.migration -t corkern-migration . >/dev/null
	docker run --rm -v "$(CURDIR)/tmp.local/v1-code.tar:/v1-code.tar:ro" \
	  $(if $(REAL_V1_EXPORT),-v "$(abspath $(REAL_V1_EXPORT)):/real-v1-export.tar:ro") corkern-migration

# migrate/ scripts 1, 2 and 3 against ~55 + 15 + 14 messy states (failed stash pops, half-finished git commands,
# other machines pushing, odd files, reruns), judged by invariants — see docker/migration/scenarios.sh
test-scenarios:
	@mkdir -p tmp.local && git -C $(V1_SRC) archive $(V1_REF) -o $(CURDIR)/tmp.local/v1-code.tar
	docker build -q -f docker/Dockerfile.migration -t corkern-migration . >/dev/null
	docker run --rm -v "$(CURDIR)/tmp.local/v1-code.tar:/v1-code.tar:ro" corkern-migration bash /src/docker/migration/scenarios.sh
	docker run --rm -v "$(CURDIR)/tmp.local/v1-code.tar:/v1-code.tar:ro" corkern-migration bash /src/docker/migration/scenarios2.sh
	docker run --rm -v "$(CURDIR)/tmp.local/v1-code.tar:/v1-code.tar:ro" corkern-migration bash /src/docker/migration/scenarios3.sh

# The PUBLISHED package (PyPI / TestPyPI), installed as users do, on fresh devices in Docker:
# no corkern source in the image, no host folder mounted (real memories are out of reach).
INDEX ?= pypi
test-published:
	@[ -n "$(VERSION)" ] || { echo "usage: make test-published VERSION=2.0.0rc2 [INDEX=pypi|testpypi]"; exit 2; }
	docker build -q -f docker/Dockerfile.published -t corkern-published . >/dev/null
	docker run --rm -e VERSION="$(VERSION)" -e INDEX="$(INDEX)" corkern-published
