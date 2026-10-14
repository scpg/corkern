""".github/workflows/ci.yml: every actions/checkout is pinned by commit SHA and keeps no credentials.

Checked on the real file (no problem) and on copies broken for each rule (the rule reports it).
"""
import re
from pathlib import Path

import pytest

CI = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text()
PINNED_CHECKOUT = re.compile(r"uses: actions/checkout@[0-9a-f]{40} # v\d+(\.\d+)*$")


def checkout_problems(text: str) -> list[str]:
    """One label per checkout step that is unpinned or keeps the token in .git/config."""
    found, lines = [], text.splitlines()
    for n, line in enumerate(lines):
        if "actions/checkout@" not in line:
            continue
        if not PINNED_CHECKOUT.search(line):
            found.append(f"unpinned:{n + 1}")
        indent = len(line) - len(line.lstrip()) + 2          # the step's keys, past "- "
        step = []
        for following in lines[n + 1:]:
            if following.strip() and len(following) - len(following.lstrip()) < indent:
                break
            if following.lstrip().startswith("- "):
                break
            step.append(following.strip())
        if "persist-credentials: false" not in step:
            found.append(f"persist-credentials:{n + 1}")
    return found


def test_ci_checkouts_are_pinned_and_keep_no_credentials():
    assert CI.count("actions/checkout@") >= 5
    assert checkout_problems(CI) == []


@pytest.mark.parametrize("old, new, rule", [
    ("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1", "actions/checkout@v4", "unpinned"),
    ("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1", "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "unpinned"),
    ("        with:\n          persist-credentials: false\n", "", "persist-credentials"),
    ("persist-credentials: false", "persist-credentials: true", "persist-credentials"),
])
def test_each_rule_catches_its_mutation(old, new, rule):
    assert old in CI
    assert any(p.startswith(rule) for p in checkout_problems(CI.replace(old, new, 1)))


# every third-party action, not only checkout ─────────────────────────────────────────────────
PINNED_ACTION = re.compile(r"uses: [\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$")


def unpinned_actions(text: str) -> list[str]:
    """`uses:` lines of third-party actions not pinned by commit SHA with a version comment."""
    return [line.strip() for line in text.splitlines()
            if "uses:" in line and "uses: ./" not in line and not PINNED_ACTION.search(line)]


def test_ci_actions_are_all_pinned():
    assert unpinned_actions(CI) == []


@pytest.mark.parametrize("old, new", [
    ("astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884 # v10.3.0", "astral-sh/setup-uv@v5"),
    ("astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884 # v10.3.0", "astral-sh/setup-uv@main"),
])
def test_unpinned_action_is_reported(old, new):
    assert old in CI
    assert unpinned_actions(CI.replace(old, new, 1)) == [f"- uses: {new}"]
