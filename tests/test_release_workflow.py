""".github/workflows/release.yml and release-gate.yml keep their security properties.

PyYAML is not a dependency, so the workflows are read line by line: jobs are the keys indented
two spaces under `jobs:`, a `run:` block is every deeper-indented line after it. That is enough
for these files, which are written in plain block style. Every rule is checked on the real files
(no problem) and on a copy broken for exactly that rule (the rule reports it), so a rule that
stops working fails here.
"""
import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
RELEASE = (WORKFLOWS / "release.yml").read_text()
GATE = (WORKFLOWS / "release-gate.yml").read_text()
CI = (WORKFLOWS / "ci.yml").read_text()

PUBLISH_JOBS = {"publish-testpypi": "testpypi", "publish-pypi": "pypi"}
NEEDS = {"publish-testpypi": "[check, gate, build]", "publish-pypi": "[check, gate, build, publish-testpypi]"}
# publish-pypi's `if:`: a skipped need (publish-testpypi of a final version) skips the job unless
# the condition has a status function; `!cancelled()` plus explicit results keeps "never publish
# after a failed check / gate / build / TestPyPI upload" (always() would publish after a failure)
PYPI_ALL_OF = ["!cancelled()"] + [f"needs.{job}.result == 'success'" for job in ("check", "gate", "build")]
STATUS_FUNCTION = re.compile(r"\b(always|cancelled|success|failure)\(\)")
PYPI_PRERELEASE = "(needs.check.outputs.prerelease == 'true' && needs.publish-testpypi.result == 'success')"
PYPI_FINAL = "(needs.check.outputs.prerelease == 'false' && needs.publish-testpypi.result == 'skipped')"
PINNED_USES = re.compile(r"uses: [\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$")
# uv writes dist/.gitignore unless told not to; the strict dist check refuses it (it once stopped
# a release at `build`): release.yml and the gate (every pull request) build the same way
UV_BUILD = "uv build --no-create-gitignore -o dist"
DIST_CHECK = 'check_release_version.py dist --version "$VERSION" dist'
SETUP_UV = re.compile(r'uses: (astral-sh/setup-uv@[0-9a-f]{40} # v[\d.]+)\n\s+with:\n\s+version: "([^"]+)"')


def jobs(text: str) -> dict[str, str]:
    """Job id → the job's lines (as text)."""
    lines = text.splitlines()
    start = lines.index("jobs:")
    found, current = {}, None
    for line in lines[start + 1:]:
        match = re.fullmatch(r"  ([\w-]+):", line)
        if match:
            current = match.group(1)
            found[current] = []
        elif current and (line.startswith("   ") or not line.strip()):
            found[current].append(line)
        elif line.strip():
            current = None
    return {name: "\n".join(body) for name, body in found.items()}


def run_blocks(text: str) -> list[str]:
    """The shell text of every `run:` step (inline or block scalar)."""
    blocks, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        match = re.match(r"(\s*)(- )?run:\s*(.*)$", lines[i])
        i += 1
        if not match:
            continue
        indent = len(match.group(1)) + (2 if match.group(2) else 0)
        if match.group(3) not in ("|", ">", "|-", ">-"):
            blocks.append(match.group(3))
            continue
        body = []
        while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
            body.append(lines[i])
            i += 1
        blocks.append("\n".join(body))
    return blocks


def permissions(job: str) -> dict[str, str]:
    """The job's `permissions:` mapping ({} when written `permissions: {}`), or None if absent."""
    lines = job.splitlines()
    for n, line in enumerate(lines):
        match = re.fullmatch(r"    permissions:\s*(\{\})?\s*(#.*)?", line)
        if match:
            perms = {}
            for entry in lines[n + 1:]:
                m = re.fullmatch(r"      ([\w-]+): (\w+)\s*(#.*)?", entry)
                if not m:
                    break
                perms[m.group(1)] = m.group(2)
            return perms
    return None


def job_if(job: str) -> str:
    """The job's `if:` condition, whitespace collapsed (inline or a `>-` block scalar)."""
    lines = job.splitlines()
    for n, line in enumerate(lines):
        match = re.fullmatch(r"    if:\s*(.*)", line)
        if not match:
            continue
        if match.group(1) not in (">-", ">", "|", "|-"):
            return match.group(1).strip()
        body = []
        for entry in lines[n + 1:]:
            if entry.strip() and len(entry) - len(entry.lstrip()) <= 4:
                break
            body.append(entry)
        return " ".join(" ".join(body).split())
    return ""


def top_level(expr: str, op: str) -> list[str]:
    """`expr` split on `op` outside parentheses, each part stripped."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(expr):
        if expr[i] == "(":
            depth += 1
        elif expr[i] == ")":
            depth -= 1
        elif depth == 0 and expr.startswith(op, i):
            parts.append(expr[start:i].strip())
            start = i = i + len(op)
            continue
        i += 1
    return parts + [expr[start:].strip()]


def pypi_if_problems(cond: str) -> list[str]:
    """publish-pypi runs only after check, gate and build succeeded, and for a pre-release only
    after the TestPyPI upload succeeded; never with always()."""
    found = []
    terms = top_level(cond, " && ")
    status = [t for t in terms if STATUS_FUNCTION.search(t)]
    if "always()" in cond or status != ["!cancelled()"]:
        found.append("pypi-if:not-cancelled")
    for job in ("check", "gate", "build"):
        if f"needs.{job}.result == 'success'" not in terms:
            found.append(f"pypi-if:{job}-success")
    branches = [t for t in terms if t not in PYPI_ALL_OF and t not in status]
    either = top_level(branches[0][1:-1], " || ") if len(branches) == 1 and branches[0].startswith("(") \
        and branches[0].endswith(")") else []
    if len(either) != 2 or any(t.count("(") != t.count(")") for t in either):
        found.append("pypi-if:branches")
        return found
    if either[0] != PYPI_PRERELEASE:
        found.append("pypi-if:prerelease-after-testpypi")
    if either[1] != PYPI_FINAL:
        found.append("pypi-if:final-without-testpypi")
    return found


def problems(release: str, gate: str) -> list[str]:
    """Every broken rule in release.yml / release-gate.yml, as a short label."""
    found = []
    both = release + "\n" + gate
    # trigger: tags v* only, never pull_request_target
    on = release.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    if on.strip() != 'push:\n    tags: ["v*"]':
        found.append("trigger")
    if "pull_request_target" in both:
        found.append("pull_request_target")
    if "\npermissions: {}\n" not in release:
        found.append("default-permissions")
    if "group: release-${{ github.ref }}" not in release or "cancel-in-progress: false" not in release:
        found.append("concurrency")
    # no secrets, no tokens: Trusted Publishing only
    if re.search(r"secrets\.|password:|api-token|GITHUB_TOKEN|github\.token", both):
        found.append("secrets")
    # no expression is interpolated into a shell script
    if any("${{" in block for block in run_blocks(both)):
        found.append("run-interpolation")
    # every third-party action pinned by full commit SHA with a version comment
    for line in both.splitlines():
        if "uses:" in line and "uses: ./" not in line and not PINNED_USES.search(line):
            found.append("unpinned")
    for name, job in {**jobs(release), **{f"gate:{k}": v for k, v in jobs(gate).items()}}.items():
        perms = permissions(job)
        if perms is None:
            found.append(f"no-permissions:{name}")
        elif name in PUBLISH_JOBS:
            if perms != {"id-token": "write"}:
                found.append(f"publish-permissions:{name}")
        elif perms != {"contents": "read"}:
            found.append(f"permissions:{name}")
        if job.count("actions/checkout@") != job.count("persist-credentials: false"):
            found.append(f"persist-credentials:{name}")
    rjobs = jobs(release)
    if set(rjobs) != {"check", "gate", "build", *PUBLISH_JOBS}:
        found.append("jobs")
        return found
    if "uses: ./.github/workflows/release-gate.yml" not in rjobs["gate"]:
        found.append("gate-call")
    if "check_release_version.py tag --tag \"$GITHUB_REF_NAME\" --branch origin/main" not in rjobs["check"] \
            or "fetch-depth: 0" not in rjobs["check"]:
        found.append("check")
    build = rjobs["build"]
    steps = ["uv build", "check_release_version.py dist --version \"$VERSION\" dist",
             "scripts/release_gate.py dist", "sha256sum", "GITHUB_STEP_SUMMARY", "actions/upload-artifact@"]
    positions = [build.find(s) for s in steps]
    if -1 in positions or positions != sorted(positions) or build.count("uv build") != 1:
        found.append("build")
    if "needs: check" not in build:
        found.append("build-needs")
    if UV_BUILD not in build:
        found.append("no-create-gitignore:release")
    # the gate builds exactly like the release and runs the same strict dist check, with the
    # version from pyproject.toml (no tag on a pull request)
    gjob = jobs(gate).get("release-gate", "")
    order = [gjob.find(s) for s in (UV_BUILD, "tomllib", "pyproject.toml", DIST_CHECK, "scripts/release_gate.py dist")]
    if -1 in order or order != sorted(order) or gjob.count("uv build") != 1:
        found.append("gate-build")
    # one setup-uv pin and one uv version for every build
    pins = {m.groups() for m in SETUP_UV.finditer(build + "\n" + gjob)}
    if len(pins) != 1 or len(SETUP_UV.findall(build)) != 1 or len(SETUP_UV.findall(gjob)) != 1:
        found.append("uv-pins")
    for name, env in PUBLISH_JOBS.items():
        job = rjobs[name]
        if not re.search(rf"^    needs: {re.escape(NEEDS[name])}$", job, re.MULTILINE):
            found.append(f"needs:{name}")
        if f"environment:\n      name: {env}" not in job:
            found.append(f"environment:{name}")
        if "uv build" in job or "actions/checkout@" in job or "actions/download-artifact@" not in job:
            found.append(f"rebuild:{name}")
        if "pypa/gh-action-pypi-publish@" not in job or "attestations: true" not in job:
            found.append(f"publish:{name}")
    if "if: needs.check.outputs.prerelease == 'true'" not in rjobs["publish-testpypi"] \
            or "repository-url: https://test.pypi.org/legacy/" not in rjobs["publish-testpypi"]:
        found.append("testpypi-target")
    if "repository-url: https://upload.pypi.org/legacy/" not in rjobs["publish-pypi"] \
            or "test.pypi.org" in rjobs["publish-pypi"]:
        found.append("pypi-target")
    found += pypi_if_problems(job_if(rjobs["publish-pypi"]))
    # the gate is reusable and only reusable
    if "\non:\n  workflow_call:\n" not in gate or gate.count("\n  push:") or gate.count("\n  pull_request"):
        found.append("gate-trigger")
    return found


def test_real_workflows_have_no_problems():
    assert problems(RELEASE, GATE) == []


def _swap(text: str, old: str, new: str, count: int = 1) -> str:
    assert old in text, old
    return text.replace(old, new, count)


MUTATIONS = [
    ("trigger", "release", 'tags: ["v*"]', 'tags: ["v*"]\n  workflow_dispatch:'),
    ("trigger", "release", 'tags: ["v*"]', 'tags: ["*"]'),
    ("pull_request_target", "gate", "  workflow_call:", "  workflow_call:\n  pull_request_target:"),
    ("default-permissions", "release", "\npermissions: {}\n", "\npermissions: write-all\n"),
    ("concurrency", "release", "cancel-in-progress: false", "cancel-in-progress: true"),
    ("secrets", "release", "attestations: true", "attestations: true\n          password: ${{ secrets.PYPI }}"),
    ("run-interpolation", "release", '--tag "$GITHUB_REF_NAME"', '--tag "${{ github.ref_name }}"'),
    ("run-interpolation", "release", "echo '```'\n", "echo '```'\n            echo ${{ github.event.head_commit.message }}\n"),
    ("unpinned", "release", "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2",
     "pypa/gh-action-pypi-publish@release/v1"),
    ("unpinned", "gate", "astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884 # v10.3.0", "astral-sh/setup-uv@v10"),
    ("publish-permissions:publish-pypi", "release", "id-token: write   # Trusted Publishing; nothing else\n    steps:\n      - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2\n        with:\n          name: dist\n          path: dist/\n      - uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2\n        with:\n          repository-url: https://upload",
     "id-token: write   # Trusted Publishing; nothing else\n      contents: write\n    steps:\n      - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2\n        with:\n          name: dist\n          path: dist/\n      - uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2\n        with:\n          repository-url: https://upload"),
    ("permissions:build", "release", "    permissions:\n      contents: read\n    env:\n      VERSION",
     "    permissions:\n      contents: read\n      id-token: write\n    env:\n      VERSION"),
    ("no-permissions:check", "release", "  check:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n",
     "  check:\n    runs-on: ubuntu-latest\n"),
    ("persist-credentials:build", "release", "        with:\n          persist-credentials: false\n      - uses: astral-sh", "      - uses: astral-sh"),
    ("persist-credentials:gate:release-gate", "gate", "          persist-credentials: false\n", ""),
    ("gate-call", "release", "    uses: ./.github/workflows/release-gate.yml", "    runs-on: ubuntu-latest\n    steps: []"),
    ("check", "release", "          fetch-depth: 0            # origin/main", "          fetch-depth: 1            # origin/main"),
    ("build", "release", "      - name: Release gate on the files that will be published\n        run: .venv/bin/python scripts/release_gate.py dist\n", ""),
    ("build", "release", "      - name: dist holds exactly", "      - run: uv build -o dist\n      - name: dist holds exactly"),
    ("build-needs", "release", "  build:\n    needs: check\n", "  build:\n"),
    ("needs:publish-pypi", "release", "  publish-pypi:\n    needs: [check, gate, build, publish-testpypi]",
     "  publish-pypi:\n    needs: [check, build, publish-testpypi]"),
    ("environment:publish-testpypi", "release", "      name: testpypi\n", "      name: pypi\n"),
    ("rebuild:publish-testpypi", "release", "    steps:\n      - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2",
     "    steps:\n      - run: uv build -o dist"),
    ("publish:publish-testpypi", "release", "attestations: true", "attestations: false"),
    ("testpypi-target", "release", "if: needs.check.outputs.prerelease == 'true'", "if: always()"),
    ("pypi-target", "release", "repository-url: https://upload.pypi.org/legacy/", "repository-url: https://test.pypi.org/legacy/"),
    ("gate-trigger", "gate", "  workflow_call:\n", "  workflow_call:\n  push:\n"),
    ("no-create-gitignore:release", "release", "uv build --no-create-gitignore -o dist", "uv build -o dist"),
    ("gate-build", "gate", "uv build --no-create-gitignore -o dist", "uv build -o dist"),
    ("gate-build", "gate", '          .venv/bin/python scripts/check_release_version.py dist --version "$VERSION" dist\n', ""),
    ("gate-build", "gate", 'print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])', 'print("2.0.0rc1")'),
    ("gate-build", "gate", "uv build --no-create-gitignore -o dist\n", "uv build --no-create-gitignore -o dist\n          uv build -o dist\n"),
    ("uv-pins", "gate", 'version: "0.11.23"', 'version: "0.11.22"'),
    ("uv-pins", "release", "astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884 # v10.3.0",
     "astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567 # v10.2.0"),
]


# publish-pypi: a pre-release reaches pypi.org only after TestPyPI, a final version without it;
# each mutation breaks exactly one rule
PYPI = "  publish-pypi:\n"
PYPI_MUTATIONS = [
    ("needs:publish-pypi", "needs: [check, gate, build, publish-testpypi]", "needs: [check, gate, build]"),
    ("pypi-if:not-cancelled", "      !cancelled()\n", "      always()\n"),
    ("pypi-if:not-cancelled", "      !cancelled()\n", "      !cancelled()\n      && always()\n"),
    ("pypi-if:check-success", "needs.check.result == 'success' && ", ""),
    ("pypi-if:gate-success", " && needs.gate.result == 'success'", ""),
    ("pypi-if:build-success", "      && needs.build.result == 'success'\n", ""),
    ("pypi-if:prerelease-after-testpypi", "'true'  && needs.publish-testpypi.result == 'success')", "'true')"),
    ("pypi-if:prerelease-after-testpypi", "'true'  && needs.publish-testpypi.result == 'success')",
     "'true'  && needs.publish-testpypi.result != 'failure')"),
    ("pypi-if:final-without-testpypi", "needs.publish-testpypi.result == 'skipped'",
     "needs.publish-testpypi.result != 'success'"),
    ("pypi-if:branches", "       || (needs.check.outputs.prerelease == 'false'", "       || true || (needs.check.outputs.prerelease == 'false'"),
    ("environment:publish-pypi", "      name: pypi        #", "      name: testpypi        #"),
    ("pypi-target", "repository-url: https://upload.pypi.org/legacy/", "repository-url: https://test.pypi.org/legacy/"),
    ("pypi-target", "url: https://pypi.org/project/corkern/", "url: https://test.pypi.org/project/corkern/"),
]


@pytest.mark.parametrize("rule, old, new", PYPI_MUTATIONS, ids=[f"{m[0]}-{n}" for n, m in enumerate(PYPI_MUTATIONS)])
def test_each_publish_pypi_rule_catches_exactly_its_mutation(rule, old, new):
    head, job = RELEASE.split(PYPI, 1)
    assert old in job, old
    assert problems(head + PYPI + job.replace(old, new, 1), GATE) == [rule]


@pytest.mark.parametrize("rule, target, old, new", MUTATIONS, ids=[f"{m[0]}-{n}" for n, m in enumerate(MUTATIONS)])
def test_each_rule_catches_its_mutation(rule, target, old, new):
    release = _swap(RELEASE, old, new) if target == "release" else RELEASE
    gate = _swap(GATE, old, new) if target == "gate" else GATE
    assert rule in problems(release, gate)


# ci.yml runs the same gate, unweakened ───────────────────────────────────────────────────────
GATE_CHECKS = [
    'BANDIT: "bandit==1.9.4"',
    'PIP_AUDIT: "pip-audit==2.10.1"',
    'GITLEAKS_SHA256: "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"',
    'SHELLCHECK_SHA256: "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198"',
    'sha256sum -c -',
    "run: .venv/bin/python scripts/release_gate.py dist",
    'run: uvx --from "$BANDIT" bandit -r src/corkern -ll',
    "run: shellcheck -x hooks/*.sh",
    "--no-deps --disable-pip --strict",
    'run: gitleaks git --no-banner --redact --log-opts="--all" .',
    'gitleaks dir --no-banner --redact "$RUNNER_TEMP/unpacked"',
    "fetch-depth: 0",
]


def test_ci_calls_the_reusable_gate():
    ci_gate = jobs(CI)["release-gate"]
    assert "uses: ./.github/workflows/release-gate.yml" in ci_gate
    assert "steps:" not in ci_gate      # the steps live only in release-gate.yml


@pytest.mark.parametrize("check", GATE_CHECKS)
def test_gate_keeps_every_check(check):
    assert check in GATE


def test_ci_actionlint_is_pinned():
    job = jobs(CI)["actionlint"]
    assert 'ACTIONLINT_VERSION: "1.7.12"' in job and "sha256sum -c -" in job
    assert re.search(r'ACTIONLINT_SHA256: "[0-9a-f]{64}"', job)


def test_the_flag_is_in_the_pinned_uv():
    """--no-create-gitignore exists in the uv version both builds pin (checked against `uv build
    --help` of that version when it is the one installed here)."""
    import shutil
    import subprocess
    pinned = SETUP_UV.search(GATE).group(2)
    uv = shutil.which("uv")
    if not uv or pinned not in subprocess.run([uv, "--version"], capture_output=True, text=True).stdout:
        pytest.skip(f"uv {pinned} not installed here")
    assert "--no-create-gitignore" in subprocess.run([uv, "build", "--help"], capture_output=True, text=True).stdout
