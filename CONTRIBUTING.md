# Contributing to corkern

Thank you for your interest. corkern is a release candidate maintained by one person, so please
read this before opening a pull request — it saves both of us a round trip.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Security issues

Do **not** open a public issue for a vulnerability, a leaked secret or a way to get memories off
a machine. Report it privately: **Security** tab → **Report a vulnerability**.

## Before you start

- **Bugs:** open an issue with the corkern version (`corkern --version`), the OS, what you ran,
  what you expected and what happened. `corkern status` output helps; check it for anything
  private (paths, project names) before pasting.
- **Changes beyond a small fix:** open an issue first and describe the idea. The architecture
  decisions listed in [CLAUDE.md](CLAUDE.md) (Python + Typer + uv, bash hooks, a separate private
  knowledge repo, GitHub as the backend) are settled; proposals to change them are unlikely to be
  accepted.

## Development setup

Requirements: Python ≥ 3.10, [uv](https://docs.astral.sh/uv/), git, and Docker for the
container suites.

    make install-dev          # uv venv --seed + editable install with dev extras
    make test                 # unit suite (.venv/bin/pytest tests/ -q)

Run the suites in a clean container before a larger change:

    make test-docker          # unit suite in a clean container
    make test-e2e             # two devices syncing through one remote
    make test-multi           # five devices syncing concurrently
    make test-teams           # several knowledge repos, planted secrets, an intruder
    make test-claude          # real Claude Code (layer 2 only with a local .env token)

The parity, migration and scenario tests need the private v1 code; without it they skip or are
local-only. That is expected — you do not need v1 to contribute.

## Rules the code depends on

These are enforced by tests or CI; a pull request that breaks them will not pass.

- **Never touch real user files.** Point `CORKERN_MEMORY_DIR` at a throw-away test knowledge repo.
  Tests must not read or write the real `~/.claude` (`real_user_files_untouched` in
  `tests/conftest.py`) and use an isolated git config (`isolated_git` fixture).
- **`src/corkern/guard.py` is the only module that touches the filesystem or starts processes**
  (`tests/test_guard_enforcement.py`). External tools run only through
  `guard.run_tool` / `capture_tool` / `exec_tool` and their allow-list. Loosening a guard rule is
  a deliberate change: explain it in the pull request.
- **Hooks never block Claude Code:** they always exit 0, and only the session-start context writes
  to stdout.
- **Hooks stay bash** and must pass `shellcheck -x`.
- **New dependencies** (Python packages, Docker image packages, GitHub Actions) need a reason in
  the pull request and a check for known vulnerabilities. GitHub Actions must be pinned by full
  commit SHA with the version in a comment (`uses: owner/action@<sha> # vX.Y.Z`); the repository
  refuses unpinned actions.
- **Nothing private in the package.** The [release gate](docs/release-gate.md) checks what would
  be published (allow-list, secret patterns, private markers, bandit, pip-audit, gitleaks). Test
  data must be synthetic; a reviewed false positive goes into `.gitleaksignore` by fingerprint.

## Pull requests

- Branch from `main`; open the pull request against `main`. Direct pushes to `main` are refused.
- **Every commit must be signed** (SSH, GPG or S/MIME) and show as *Verified* on GitHub. See
  [Signing commits](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits).
- **All CI checks must pass and the branch must be up to date with `main`** before it can merge.
  Workflows on pull requests from first-time and outside contributors wait for the maintainer's
  approval before they run.
- Keep one topic per pull request, with tests for the change. Update the docs it affects
  (`README.md`, `docs/`, the status table in `CLAUDE.md`).
- Commit messages follow the style already in the history: `type(scope): summary`, for example
  `fix(secrets): …`, `docs(releasing): …`, `build(dev): …`, `test(install): …`.

## Licence

By contributing you agree that your contribution is licensed under the [MIT Licence](LICENSE)
of this project.
