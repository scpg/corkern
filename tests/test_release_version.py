"""scripts/check_release_version.py — the `check` and `build` gates of release.yml.

Each check fails on a case built to break exactly that check: a tag/version mismatch, an invalid
or non-normalized version, a tag off main, a dist folder with a missing or extra file.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_release_version as crv  # noqa: E402

SCRIPT = str(ROOT / "scripts" / "check_release_version.py")


# PEP 440 and pre-release detection ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, prerelease", [
    ("2.0.0", False), ("2.0", False), ("1!2.0.0", False), ("2.0.0.post1", False),
    ("2.0.0rc1", True), ("2.0.0a1", True), ("2.0.0b12", True), ("2.0.0.dev3", True),
    ("2.0.0rc1.post1", True), ("2.0.0.post1.dev1", True),
])
def test_valid_versions_and_prerelease_detection(text, prerelease):
    version, reason = crv.parse_version(text)
    assert reason == "" and version == crv.Version(text, prerelease)


@pytest.mark.parametrize("text", ["", "2.0.0x1", "2.0.0-foo", "two", "2..0", ".2.0", "v2.0.0", "2.0.0 "])
def test_invalid_versions_fail(text):
    version, reason = crv.parse_version(text)
    assert version is None and "not a valid PEP 440 version" in reason


@pytest.mark.parametrize("text", ["2.0.0-rc1", "2.0.0RC1", "2.0.0c1", "2.0.0alpha1", "02.0.0", "2.0.0-1", "2.0.0.rc1"])
def test_valid_but_not_normalized_fails(text):
    version, reason = crv.parse_version(text)
    assert version is None and "not in normalized form" in reason


def test_local_version_fails():
    version, reason = crv.parse_version("2.0.0+local.1")
    assert version is None and "local version" in reason


# tag == v + pyproject version == __version__ ────────────────────────────────────────────────
def test_matching_tag_passes():
    assert crv.check_tag("v2.0.0rc1", "2.0.0rc1", "2.0.0rc1") == (crv.Version("2.0.0rc1", True), [])
    assert crv.check_tag("v2.0.0", "2.0.0", "2.0.0") == (crv.Version("2.0.0", False), [])


def test_tag_version_mismatch_fails():
    version, errors = crv.check_tag("v2.0.0", "2.0.0rc1", "2.0.0rc1")
    assert version is None and errors == ["tag 'v2.0.0' != 'v' + pyproject.toml version '2.0.0rc1'"]


def test_pyproject_and_package_version_mismatch_fails():
    version, errors = crv.check_tag("v2.0.0rc1", "2.0.0rc1", "2.0.0rc2")
    assert version is None and errors == ["pyproject.toml version '2.0.0rc1' != __version__ '2.0.0rc2'"]


def test_invalid_version_with_matching_tag_fails():
    version, errors = crv.check_tag("v2.0.0.rc1", "2.0.0.rc1", "2.0.0.rc1")
    assert version is None and len(errors) == 1 and "not in normalized form" in errors[0]


def test_missing_versions_fail():
    version, errors = crv.check_tag("v2.0.0", None, None)
    assert version is None and len(errors) == 2


@pytest.mark.parametrize("tag", ["2.0.0", "v", "va.b", "v2.0.0;id", "v2.0.0 x", "v2.0.0$(id)", "v2.0.0/../x",
                                 "v2.0.0\n", "V2.0.0", "v-2", "v2.0.0^{}", "v" + "1" * 70])
def test_malformed_tag_names_fail_before_any_comparison(tag):
    version, errors = crv.check_tag(tag, tag[1:], tag[1:])
    assert version is None and len(errors) == 1 and "is not 'v' followed by a version" in errors[0]


def test_repo_versions_agree():
    """The real pyproject.toml and __init__.py are read, and agree."""
    pyproject, package = crv.read_pyproject_version(), crv.read_package_version()
    assert pyproject and pyproject == package
    assert crv.parse_version(pyproject)[0] is not None


def test_pyproject_reader_only_reads_the_project_table(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.x]\nversion = "9.9.9"\n\n[project]\nname = "corkern"\nversion = "2.0.0"  # note\n')
    assert crv.read_pyproject_version(path) == "2.0.0"
    path.write_text('[tool.x]\nversion = "9.9.9"\n[project]\nname = "corkern"\n')
    assert crv.read_pyproject_version(path) is None


# the tag is on main ─────────────────────────────────────────────────────────────────────────
def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(repo),
                        "PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


@pytest.fixture
def repo(tmp_path):
    """main with one commit, a side branch with one more; origin/main mirrors main."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "on main")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "main")
    _git(tmp_path, "checkout", "-q", "-b", "side")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "off main")
    return tmp_path


def test_tag_on_main_passes(repo):
    _git(repo, "tag", "-a", "-m", "release", "v2.0.0", "main")      # annotated: peeled to its commit
    assert crv.tag_on_branch("v2.0.0", "origin/main", repo) == (True, "")


def test_tag_off_main_fails(repo):
    _git(repo, "tag", "v2.0.0", "side")
    ok, reason = crv.tag_on_branch("v2.0.0", "origin/main", repo)
    assert not ok and "is not on origin/main" in reason


def test_missing_tag_or_branch_fails(repo):
    _git(repo, "tag", "v2.0.0", "main")
    assert crv.tag_on_branch("v9.9.9", "origin/main", repo)[1] == "tag 'v9.9.9' not found in the checkout"
    assert "not found in the checkout" in crv.tag_on_branch("v2.0.0", "origin/nope", repo)[1]


# dist holds exactly this version's files ────────────────────────────────────────────────────
def _dist(tmp_path, *names):
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in names:
        (dist / name).write_bytes(b"x")
    return dist


WHEEL, SDIST = "corkern-2.0.0rc1-py3-none-any.whl", "corkern-2.0.0rc1.tar.gz"


def test_dist_with_exactly_the_two_files_passes(tmp_path):
    assert crv.check_dist(_dist(tmp_path, WHEEL, SDIST), "2.0.0rc1") == []


def test_dist_missing_or_extra_or_other_version_fails(tmp_path):
    dist = _dist(tmp_path, SDIST, "corkern-2.0.0-py3-none-any.whl", "SHA256SUMS")
    assert crv.check_dist(dist, "2.0.0rc1") == [
        f"missing from {dist}: {WHEEL}",
        f"unexpected in {dist}: SHA256SUMS",
        f"unexpected in {dist}: corkern-2.0.0-py3-none-any.whl",
    ]


# command line (as release.yml calls it) ─────────────────────────────────────────────────────
def test_cli_dist(tmp_path):
    dist = _dist(tmp_path, WHEEL, SDIST)
    ok = subprocess.run([sys.executable, SCRIPT, "dist", "--version", "2.0.0rc1", str(dist)], capture_output=True, text=True)
    assert ok.returncode == 0
    bad = subprocess.run([sys.executable, SCRIPT, "dist", "--version", "2.0.0", str(dist)], capture_output=True, text=True)
    assert bad.returncode == 1 and "missing" in bad.stderr


def test_cli_tag_mismatch_fails_and_writes_no_output(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert crv.main(["tag", "--tag", "v0.0.1", "--branch", "origin/main"]) == 1
    assert not out.exists()


def test_cli_tag_writes_outputs(tmp_path, monkeypatch):
    """Version and pre-release flag go to GITHUB_OUTPUT for the publish jobs' `if:`."""
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    version = crv.read_pyproject_version()
    monkeypatch.setattr(crv, "tag_on_branch", lambda tag, branch: (True, ""))
    assert crv.main(["tag", "--tag", f"v{version}", "--branch", "origin/main"]) == 0
    prerelease = "true" if crv.parse_version(version)[0].prerelease else "false"
    assert out.read_text() == f"version={version}\nprerelease={prerelease}\n"


def test_cli_tag_off_branch_fails(monkeypatch, capsys):
    monkeypatch.setattr(crv, "tag_on_branch", lambda tag, branch: (False, "tag 'v' is not on origin/main"))
    assert crv.main(["tag", "--tag", f"v{crv.read_pyproject_version()}", "--branch", "origin/main"]) == 1
    assert "is not on origin/main" in capsys.readouterr().err


@pytest.mark.parametrize("version, kind", [("2.0.0rc1", "pre-release (TestPyPI, then PyPI after approval)"),
                                           ("2.0.0", "final (PyPI)")])
def test_cli_tag_prints_where_the_release_goes(version, kind, monkeypatch, capsys):
    monkeypatch.setattr(crv, "read_pyproject_version", lambda: version)
    monkeypatch.setattr(crv, "read_package_version", lambda: version)
    monkeypatch.setattr(crv, "tag_on_branch", lambda tag, branch: (True, ""))
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert crv.main(["tag", "--tag", f"v{version}", "--branch", "origin/main"]) == 0
    assert capsys.readouterr().out == f"corkern {version}: {kind}, tag v{version} is on origin/main\n"
