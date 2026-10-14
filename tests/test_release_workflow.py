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
PINNED_USES = re.compile(r"uses: [\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$")


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
    steps = ["uv build -o dist", "check_release_version.py dist --version \"$VERSION\" dist",
             "scripts/release_gate.py dist", "sha256sum", "GITHUB_STEP_SUMMARY", "actions/upload-artifact@"]
    positions = [build.find(s) for s in steps]
    if -1 in positions or positions != sorted(positions) or build.count("uv build") != 1:
        found.append("build")
    if "needs: check" not in build:
        found.append("build-needs")
    for name, env in PUBLISH_JOBS.items():
        job = rjobs[name]
        if "needs: [check, gate, build]" not in job:
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
    if "if: needs.check.outputs.prerelease == 'false'" not in rjobs["publish-pypi"] \
            or "repository-url: https://upload.pypi.org/legacy/" not in rjobs["publish-pypi"] \
            or "test.pypi.org" in rjobs["publish-pypi"]:
        found.append("pypi-target")
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
    ("needs:publish-pypi", "release", "  publish-pypi:\n    needs: [check, gate, build]", "  publish-pypi:\n    needs: [check, build]"),
    ("environment:publish-testpypi", "release", "      name: testpypi\n", "      name: pypi\n"),
    ("rebuild:publish-testpypi", "release", "    steps:\n      - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2",
     "    steps:\n      - run: uv build -o dist"),
    ("publish:publish-testpypi", "release", "attestations: true", "attestations: false"),
    ("testpypi-target", "release", "if: needs.check.outputs.prerelease == 'true'", "if: always()"),
    ("pypi-target", "release", "if: needs.check.outputs.prerelease == 'false'", "if: needs.check.outputs.prerelease != 'banana'"),
    ("pypi-target", "release", "repository-url: https://upload.pypi.org/legacy/", "repository-url: https://test.pypi.org/legacy/"),
    ("gate-trigger", "gate", "  workflow_call:\n", "  workflow_call:\n  push:\n"),
]


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
