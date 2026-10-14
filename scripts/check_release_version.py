"""Release checks run by .github/workflows/release.yml before anything is built or published.

    python3 scripts/check_release_version.py tag --tag "$GITHUB_REF_NAME" --branch origin/main
    python3 scripts/check_release_version.py dist --version 2.0.0rc1 dist

`tag` fails unless
  1. the tag name is well formed (`v` + a version, nothing a shell or git could misread),
  2. the tag's commit is reachable from the release branch (origin/main),
  3. the tag equals `v` + the version in pyproject.toml ([project] version), which equals
     `__version__` in src/corkern/__init__.py,
  4. that version is valid PEP 440 in normalized form, with no local part (+...): PyPI refuses
     local versions, and a normalized version is what `uv build` writes into the file names.
It prints the version and whether it is a pre-release (a, b, rc or dev: published to TestPyPI,
then to PyPI after the owner's approval; anything else is final: PyPI only), and writes both to
$GITHUB_OUTPUT when that is set.

`dist` fails unless the folder holds exactly corkern-<version>.tar.gz and
corkern-<version>-py3-none-any.whl: the files that will be published, nothing else.

Lives outside src/corkern so it never ships; standard library only, Python 3.10+.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_INIT = ROOT / "src" / "corkern" / "__init__.py"

# PEP 440, appendix B ("VERSION_PATTERN"): every version PEP 440 accepts, normalized or not
PEP440 = re.compile(r"""
    v?
    (?:
        (?:(?P<epoch>[0-9]+)!)?
        (?P<release>[0-9]+(?:\.[0-9]+)*)
        (?P<pre>[-_\.]?(?P<pre_l>(a|b|c|rc|alpha|beta|pre|preview))[-_\.]?(?P<pre_n>[0-9]+)?)?
        (?P<post>(?:-(?P<post_n1>[0-9]+))|(?:[-_\.]?(?P<post_l>post|rev|r)[-_\.]?(?P<post_n2>[0-9]+)?))?
        (?P<dev>[-_\.]?(?P<dev_l>dev)[-_\.]?(?P<dev_n>[0-9]+)?)?
    )
    (?:\+(?P<local>[a-z0-9]+(?:[-_\.][a-z0-9]+)*))?
""", re.VERBOSE | re.IGNORECASE)

# The normalized form (PEP 440 "Normalization"), without a local part
NUM = r"(?:0|[1-9][0-9]*)"
NORMALIZED = re.compile(rf"(?:{NUM}!)?{NUM}(?:\.{NUM})*(?:(?:a|b|rc){NUM})?(?:\.post{NUM})?(?:\.dev{NUM})?")
PRE_RELEASE = re.compile(rf"(?:a|b|rc){NUM}|\.dev{NUM}")

# What a tag name may look like before it is compared or handed to git
TAG_NAME = re.compile(r"v[0-9][0-9a-z.!]{0,63}")


@dataclass(frozen=True)
class Version:
    text: str
    prerelease: bool


def parse_version(text: str) -> tuple[Version | None, str]:
    """(Version, "") when `text` is a publishable version, else (None, reason)."""
    match = PEP440.fullmatch(text)
    if not match or text.lower().startswith("v"):
        return None, f"{text!r} is not a valid PEP 440 version"
    if match.group("local"):
        return None, f"{text!r} has a local version part (+...), which PyPI refuses"
    if not NORMALIZED.fullmatch(text):
        return None, f"{text!r} is valid PEP 440 but not in normalized form (e.g. 2.0.0rc1, 2.0.0.post1)"
    return Version(text, bool(PRE_RELEASE.search(text))), ""


def read_pyproject_version(path: Path = PYPROJECT) -> str | None:
    """`version = "..."` in the [project] table (a static version, as corkern has)."""
    table = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            table = stripped
        elif table == "[project]":
            match = re.fullmatch(r"""version\s*=\s*["']([^"']*)["']\s*(?:#.*)?""", stripped)
            if match:
                return match.group(1)
    return None


def read_package_version(path: Path = PACKAGE_INIT) -> str | None:
    match = re.search(r"""^__version__\s*=\s*["']([^"']*)["']""", path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def check_tag(tag: str, pyproject_version: str | None, package_version: str | None) -> tuple[Version | None, list[str]]:
    """The tag name, the two version sources and PEP 440 (checks 1, 3 and 4)."""
    if not TAG_NAME.fullmatch(tag):
        return None, [f"tag {tag!r} is not 'v' followed by a version (lowercase letters, digits, '.', '!')"]
    errors = []
    if pyproject_version is None:
        errors.append("no static [project] version in pyproject.toml")
    if package_version is None:
        errors.append("no __version__ in src/corkern/__init__.py")
    if errors:
        return None, errors
    if pyproject_version != package_version:
        errors.append(f"pyproject.toml version {pyproject_version!r} != __version__ {package_version!r}")
    if tag != f"v{pyproject_version}":
        errors.append(f"tag {tag!r} != 'v' + pyproject.toml version {pyproject_version!r}")
    version, reason = parse_version(pyproject_version)
    if version is None:
        errors.append(reason)
    return (version if not errors else None), errors


def tag_on_branch(tag: str, branch: str, repo: Path = ROOT) -> tuple[bool, str]:
    """Is the tag's commit reachable from `branch` (e.g. origin/main)? (check 2)

    Call only with a tag name check_tag accepted: it is passed to git as one argument.
    """
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)

    commit = git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    if commit.returncode != 0:
        return False, f"tag {tag!r} not found in the checkout"
    head = git("rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}")
    if head.returncode != 0:
        return False, f"branch {branch!r} not found in the checkout (fetch-depth: 0 is needed)"
    sha = commit.stdout.strip()
    if git("merge-base", "--is-ancestor", sha, head.stdout.strip()).returncode != 0:
        return False, f"tag {tag!r} ({sha[:12]}) is not on {branch}: tag a commit that was merged into it"
    return True, ""


def check_dist(dist: Path, version: str) -> list[str]:
    """Exactly this version's sdist and wheel, nothing else."""
    expected = {f"corkern-{version}.tar.gz", f"corkern-{version}-py3-none-any.whl"}
    found = {p.name for p in dist.iterdir()} if dist.is_dir() else set()
    errors = [f"missing from {dist}: {name}" for name in sorted(expected - found)]
    errors += [f"unexpected in {dist}: {name}" for name in sorted(found - expected)]
    return errors


def write_outputs(version: Version) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"version={version.text}\nprerelease={'true' if version.prerelease else 'false'}\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    tag_cmd = sub.add_parser("tag", help="the pushed tag, the version and the branch")
    tag_cmd.add_argument("--tag", required=True)
    tag_cmd.add_argument("--branch", required=True, help="e.g. origin/main")
    dist_cmd = sub.add_parser("dist", help="the built files")
    dist_cmd.add_argument("--version", required=True)
    dist_cmd.add_argument("dist", type=Path)
    args = parser.parse_args(argv)

    if args.command == "dist":
        version, reason = parse_version(args.version)
        errors = [reason] if version is None else check_dist(args.dist, version.text)
        for error in errors:
            print(f"release check: {error}", file=sys.stderr)
        if not errors:
            print(f"dist holds exactly corkern {args.version} (sdist + wheel)")
        return 1 if errors else 0

    version, errors = check_tag(args.tag, read_pyproject_version(), read_package_version())
    if version is not None:
        on_branch, reason = tag_on_branch(args.tag, args.branch)
        if not on_branch:
            errors.append(reason)
    if errors:
        for error in errors:
            print(f"release check: {error}", file=sys.stderr)
        return 1
    kind = "pre-release (TestPyPI, then PyPI after approval)" if version.prerelease else "final (PyPI)"
    print(f"corkern {version.text}: {kind}, tag {args.tag} is on {args.branch}")
    write_outputs(version)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
