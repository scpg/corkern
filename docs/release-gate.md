# Release gate

The release gate (`.github/workflows/release-gate.yml`, a reusable workflow) runs as the CI job
`release-gate` (`.github/workflows/ci.yml`) on every pull request and every push to `main`, and as
the job `gate` of `.github/workflows/release.yml` on every release tag. It checks what would be
uploaded to PyPI / TestPyPI. Nothing is uploaded by this job; the publish jobs of `release.yml`
depend on it, and `release.yml` also runs `scripts/release_gate.py` on the exact files it
publishes ([releasing.md](releasing.md)).

## What is checked

| Step | Fails when |
|---|---|
| `uv build --no-create-gitignore -o dist`, then `check_release_version.py dist` (version from `pyproject.toml`) | `dist/` holds anything but this version's sdist and wheel: the same build and check as `release.yml`'s `build` job (same pinned `setup-uv` and uv version, `tests/test_release_workflow.py`), so a pull request finds what would stop a release. Without the flag uv writes `dist/.gitignore`, which stopped the first 2.0.0rc1 run at `build` |
| `scripts/release_gate.py dist` | a file in the wheel or sdist lies outside the allow-list (shared with `tests/test_package.py`), or an sdist member is a link or device |
| | a text file matches one of corkern's own secret patterns (`corkern.secret_scan`) |
| | a text file contains a private marker (see below) |
| | a file belongs to a knowledge repo's layout (`MEMORY.md`, `.metrics.tsv`, `corkern.conf`, `.devices/`, `memory/`, `sessions/`, `todo.md`, `CLAUDE.md`, conflict copies …) or a text file holds knowledge repo data: memory frontmatter with a concrete `type:`, `MEMORY.md` index entries (`- [Title](file.md)`), registry / device manifest lines with an absolute path |
| `bandit -r src/corkern -ll` | a medium or high severity finding has no `# nosec <id>` (each one is justified on the line above it) |
| `shellcheck -x hooks/*.sh` | any shellcheck finding in the packaged hooks (`lib.sh` is followed) |
| `pip-audit` | a known vulnerability in the resolved runtime dependencies (`uv pip compile --universal pyproject.toml`) |
| `gitleaks git` | a leak anywhere in this repo's git history; reviewed false positives (synthetic test tokens) are listed by fingerprint in `.gitleaksignore` |
| `gitleaks dir` | a leak in the unpacked wheel and sdist |

Templates that `knowledge_repo.py` writes when it creates a knowledge repo are Python strings
in code and pass: they hold no memory data. A *file* with a knowledge repo name fails.

Reports name the file, the line and the kind of finding. They do not print the matched secret
or marker.

## Private markers

Private markers are strings that must never appear in a published package: your name, e-mail
addresses, user names, host names. They are not stored in this repo in plain text. Only salted
SHA-256 hashes are kept, in `scripts/private_markers.json`; the gate lowercases every text file,
extracts candidate tokens (words, e-mail addresses and their domains, host-like tokens such as
`my-laptop.lan`, `/home/<name>` segments, and windows of one to three words), hashes them with
the salt and compares.

Add markers locally, one per line, on stdin. They are never taken from the command line and
never printed:

    .venv/bin/python scripts/add_private_marker.py
    marker (empty line to finish): ********

A marker is one token or up to three words, matched case-insensitively. Markers the gate cannot
match (more than three words, or punctuation that splits the token, such as `o'brien`) are
refused and the script exits 1. Commit the updated `private_markers.json` so CI checks them too.

The hashes keep markers out of plain sight, but a short or common marker can be recovered by
anyone who hashes a list of guesses with the published salt. Treat the file as obscuring, not
encrypting, the markers.

## Run the gate locally

    uv build --no-create-gitignore -o dist \
      && .venv/bin/python scripts/check_release_version.py dist --version 2.0.0rc2 dist \
      && .venv/bin/python scripts/release_gate.py dist

The other scanners, with the versions CI uses:

    uvx --from bandit==1.9.4 bandit -r src/corkern -ll
    (cd src/corkern && shellcheck -x hooks/*.sh)
    uv pip compile --universal -q pyproject.toml -o tmp.local/runtime-requirements.txt
    uvx --from pip-audit==2.10.1 pip-audit -r tmp.local/runtime-requirements.txt --no-deps --disable-pip --strict
    gitleaks git --redact --log-opts="--branches --remotes=origin --tags" .

Locally, limit gitleaks to this repo's own refs: a clone that also fetched the `v1` remote holds
v1's memories, which are not part of this repo's history.
