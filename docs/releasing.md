# Releasing corkern

A release is a pushed tag `v<version>`. `.github/workflows/release.yml` does the rest:

| Job | What it does |
|---|---|
| `check` | `scripts/check_release_version.py tag`: fails unless the tag's commit is reachable from `origin/main`, the tag equals `v` + `[project] version` in `pyproject.toml`, that equals `__version__` in `src/corkern/__init__.py`, and the version is PEP 440 in normalized form without a local part (`+...`). Decides pre-release (`a`, `b`, `rc`, `dev`) or final. |
| `gate` | the same release gate as CI (`release-gate.yml`, see [release-gate.md](release-gate.md)) |
| `build` | `uv build --no-create-gitignore` **once** (no `dist/.gitignore`; the CI gate builds the same way); `check_release_version.py dist` (exactly `corkern-<version>.tar.gz` + `corkern-<version>-py3-none-any.whl`); `release_gate.py` on exactly those files; SHA-256 sums in the job summary; uploads them as the artifact `dist`. These files are published; nothing is rebuilt later. |
| `publish-testpypi` | pre-releases only; environment `testpypi`; publishes the artifact to test.pypi.org |
| `publish-pypi` | environment `pypi`, which **waits for the owner's approval** (required reviewer); publishes the same artifact to pypi.org. A final version: directly (`publish-testpypi` is skipped). A pre-release: only after `publish-testpypi` succeeded |

Both publish jobs need `check`, `gate` and `build` (`publish-pypi` also `publish-testpypi`), have
only `id-token: write`, and use Trusted Publishing (OIDC): there is no token or secret anywhere.
Each upload carries PEP 740 attestations. Runs for the same tag are queued, never cancelled
half-way. `publish-pypi` runs only when `check`, `gate` and `build` succeeded and, for a
pre-release, the TestPyPI upload succeeded: its condition is `!cancelled()` plus those explicit
results (a skipped need would otherwise skip it; `always()` would publish after a failure).

A pre-release goes to TestPyPI first, then, after the owner's approval, to PyPI; a final version
goes to PyPI only. Publishing pre-releases to PyPI claims the name `corkern` there before 2.0.0.
`pip` and `uv` install a pre-release only when asked for it explicitly (`corkern==2.0.0rc1` or
`--pre`), so `pip install corkern` keeps installing nothing until 2.0.0 exists. To try a final
version on TestPyPI first, release an `rc` of it.

## Cut a release

1. **Bump the version in both places, via a pull request.** `pyproject.toml` (`[project]
   version`) and `src/corkern/__init__.py` (`__version__`) must be identical and normalized
   PEP 440: `2.0.0rc1`, `2.0.0`, `2.0.1.post1` (not `2.0.0-rc1` or `2.0.0RC1`). Check:

       python3 scripts/check_release_version.py tag --tag v2.0.0rc1 --branch origin/main

   (on a branch that is not yet merged this reports "is not on origin/main"; the version checks
   are what matter here). CI must be green; merge.

2. **Tag the merged commit on `main` and push the tag:**

       git fetch origin main
       git checkout main && git pull --ff-only origin main
       git tag -s v2.0.0rc1 -m "corkern 2.0.0rc1"     # or -a if you do not sign tags
       git push origin v2.0.0rc1

   Push only the tag (`git push --tags` pushes every local tag).

3. **Pre-release (`rc`, `a`, `b`, `dev`) → TestPyPI, then PyPI after approval.** Watch the run
   under *Actions → Release*. The `build` job summary lists the SHA-256 of both files.
   `publish-testpypi` uploads them to TestPyPI; the run then stops at `publish-pypi` with
   *Waiting for review*. Verify the package on TestPyPI first, in a clean environment:

       uv tool install --index-url https://test.pypi.org/simple/ \
           --extra-index-url https://pypi.org/simple/ corkern==2.0.0rc1
       corkern --version          # corkern 2.0.0rc1
       uv tool uninstall corkern

   `--extra-index-url` is there for the dependencies (typer, rich), which are not on TestPyPI.
   Then compare the SHA-256 sums in the `build` summary and *Review deployments → pypi →
   Approve and deploy*: the same files go to pypi.org. Users get the pre-release only when they
   ask for it (`pip install corkern==2.0.0rc1` or `--pre`).

   **If something is wrong on TestPyPI:** do not approve. Cancel the run (*Cancel workflow*;
   `publish-pypi` then never runs), fix the cause through a pull request and release `rc2`
   (steps 1 and 2). TestPyPI never accepts the same file name twice.

4. **Final version → PyPI.** Bump to e.g. `2.0.0` (step 1), tag `v2.0.0` (step 2). Release the
   same code as an `rc` first and verify it on TestPyPI (step 3). The run stops at
   `publish-pypi` with *Waiting for review*: compare the SHA-256 sums in the `build` summary,
   then *Review deployments → pypi → Approve and deploy*. Nothing reaches pypi.org before that.

### When something fails

- `check` fails: nothing was built or uploaded. Delete the tag (`git push origin :refs/tags/v…`
  and `git tag -d v…`), fix the cause through a pull request, tag again.
- `gate` or `build` fails: same, nothing was uploaded.
- A publish job fails after the upload started: PyPI and TestPyPI never accept the same file
  name twice. Do not move the tag; release the next version (`rc2`, `.post1`, …).

## One-time setup (owner)

Done already:

- **Pending Trusted Publishers** on pypi.org and test.pypi.org: owner `scpg`, repository
  `corkern`, workflow `release.yml`, environment `pypi` (pypi.org) / `testpypi` (test.pypi.org).

To do in GitHub, *Settings → Environments*:

- **`testpypi`**: *Deployment branches and tags* → *Selected branches and tags* → add a **tag**
  rule `v*`. No secrets.
- **`pypi`**: the same tag rule `v*`; *Required reviewers* → add yourself (leave *Prevent
  self-review* off while you are the only reviewer) → *Save protection rules*; no secrets. This is
  the manual approval before anything reaches pypi.org.

*Settings → Rules → Rulesets → New ruleset → New tag ruleset*: name `release tags`, enforcement
*Active*, target pattern `v*`, *Restrict creations*, *Restrict updates*, *Restrict deletions*,
bypass list *Repository admin*. Only the owner can then create, move or delete a release tag.

### What protects a release

Only the owner can create, move or delete a `v*` tag (ruleset); the tag must be on `main` and
match the version (`check`); the release gate runs on the exact files (`gate`, `build`); every upload
to pypi.org, final or pre-release, waits for the owner's approval in the `pypi` environment, a
pre-release only after its TestPyPI upload succeeded. (On a private repository GitHub
offers required reviewers only on the Enterprise plan; this repository is public.)

If branch protection on `main` requires the status check `release-gate`, update it: the job now
calls a reusable workflow, so its check is reported as `release-gate / release-gate`.
