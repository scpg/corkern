## What and why

<!-- What does this change, and why is it needed? Link the issue: "Fixes #123". -->

## How it was tested

<!-- Commands run and their result, e.g. `make test`, `make test-docker`, `make test-e2e`. -->

## Checklist

- [ ] Tests added or updated for the change; `make test` passes locally
- [ ] Docs updated where behaviour changed (`README.md`, `docs/`, status table in `CLAUDE.md`)
- [ ] No real user files touched: tests use a throw-away `CORKERN_MEMORY_DIR` and never `~/.claude`
- [ ] Filesystem and process access only through `src/corkern/guard.py`
- [ ] No secrets, personal data or real memories in code, tests or fixtures
- [ ] New dependencies (if any) are justified below and checked for known vulnerabilities
- [ ] New GitHub Actions (if any) are pinned by full commit SHA
- [ ] All commits are signed

## Notes for the reviewer

<!-- Anything that needs attention: guard changes, new dependencies, open questions. -->
