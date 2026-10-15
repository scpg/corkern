"""The published packages (wheel + sdist) hold corkern's code and nothing else.

Builds both with `uv build` and checks every file name against an allow-list: tests, docs,
docker, migrate and any untracked folder (which may hold personal data) can never be shipped.
The wheel must carry the hooks and slash commands that init-device installs.
"""
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")

# One allow-list for this test and the release gate (scripts/release_gate.py)
sys.path.insert(0, str(ROOT / "scripts"))
import release_gate  # noqa: E402


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(["uv", "build", "-q", "-o", str(out), str(ROOT)], check=True, capture_output=True)
    return out


def _wheel(dist):
    return zipfile.ZipFile(next(dist.glob("*.whl"))).namelist()


def _sdist(dist):
    with tarfile.open(next(dist.glob("*.tar.gz"))) as t:
        return [n.split("/", 1)[1] for n in t.getnames() if "/" in n]


def test_wheel_contains_only_the_package(dist):
    bad = [n for n in _wheel(dist) if not release_gate.wheel_name_allowed(n)]
    assert not bad, f"not allowed in the wheel: {bad}"


def test_sdist_contains_only_the_code(dist):
    names = [n for n in _sdist(dist) if n and not n.endswith("/")]
    bad = [n for n in names if not release_gate.sdist_name_allowed(n)]
    assert not bad, f"not allowed in the sdist: {bad}"


def test_wheel_carries_hooks_and_slash_commands(dist):
    names = set(_wheel(dist))
    from corkern.hooks_install import HOOK_FILES
    for hook in HOOK_FILES:
        assert f"corkern/hooks/{hook}" in names
    assert sum(n.startswith("corkern/claude_commands/corkern-") for n in names) >= 4


def test_built_packages_pass_the_release_gate(dist):
    findings, errors = release_gate.run(dist)
    assert not errors and not findings, "\n".join(errors + [str(f) for f in findings])


NOTICE = "**First test release — please do not use it yet.**"


def test_pypi_page_warns_while_the_version_is_a_pre_release(dist):
    """What pypi.org shows is the wheel's METADATA (PYPI.md as the description). Until a proper
    version exists, every pre-release page opens with the notice and is classified Pre-Alpha."""
    whl = zipfile.ZipFile(next(dist.glob("*.whl")))
    meta = whl.read(next(n for n in whl.namelist() if n.endswith(".dist-info/METADATA"))).decode()
    version = next(line.split(": ", 1)[1] for line in meta.splitlines() if line.startswith("Version: "))
    if not any(tag in version for tag in ("a", "b", "rc", "dev")):
        pytest.skip(f"{version} is a final version")
    head, _, description = meta.partition("\n\n")
    assert description.lstrip().startswith("> ⚠ " + NOTICE), description[:200]
    assert "Classifier: Development Status :: 2 - Pre-Alpha" in head
    assert "Development Status :: 4 - Beta" not in head
