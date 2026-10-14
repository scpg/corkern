"""corkern distill — with a stub `claude` that records its call."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
C2 = ROOT / ".venv" / "bin" / "corkern"

STUB = """#!/usr/bin/env bash
echo "args=$*" >> "$CLAUDE_LOG"
if [ "$1" = "-p" ]; then
  cat > "$CLAUDE_LOG.stdin"
  [ -n "${CLAUDE_FAIL:-}" ] && exit 1
  [ -n "${CLAUDE_EMPTY:-}" ] && exit 0
  echo "## Universal Principles"; echo "- be kind"
fi
"""


@pytest.fixture
def env(tmp_path):
    kr = tmp_path / "kr"
    subprocess.run([str(C2), "init", str(kr)], check=True, capture_output=True)
    (kr / "corkern.conf").write_text("alpha:/tmp/a\nbeta:/tmp/b\n")
    (kr / "memory" / "alpha").mkdir()
    (kr / "memory" / "alpha" / "rule.md").write_text("always test\n")
    subprocess.run(["git", "-C", str(kr), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(kr), "commit", "-qm", "seed"], check=True)
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "claude").write_text(STUB)
    (stub / "claude").chmod(0o755)
    log = tmp_path / "claude.log"
    e = {**os.environ, "CORKERN_MEMORY_DIR": str(kr), "CLAUDE_LOG": str(log),
         "PATH": f"{stub}:{os.environ['PATH']}"}
    return {"kr": kr, "env": e, "log": log}


def run(env, *args, **extra):
    return subprocess.run([str(C2), "distill", *args], env={**env["env"], **extra},
                          capture_output=True, text=True, timeout=60)


def synthesis(env):
    return env["kr"] / "memory" / "global" / "synthesis.md"


def test_auto_saves_synthesis_with_frontmatter(env):
    r = run(env, "--auto")
    assert r.returncode == 0, r.stderr
    text = synthesis(env).read_text()
    assert text.startswith("---\nname: synthesis\n") and "- be kind" in text


def test_prompt_has_all_memories_and_registry_projects(env):
    run(env, "--auto", "--model", "haiku")
    prompt = Path(str(env["log"]) + ".stdin").read_text()
    assert "Projects present: alpha, beta, global" in prompt
    assert "=== memory/alpha/rule.md ===\nalways test" in prompt
    assert "args=-p --model haiku" in env["log"].read_text()


@pytest.mark.parametrize("flag", ["CLAUDE_FAIL", "CLAUDE_EMPTY"])
def test_failed_run_keeps_previous_synthesis(env, flag):
    synthesis(env).write_text("previous synthesis")
    r = run(env, "--auto", **{flag: "1"})
    assert r.returncode != 0 and "left unchanged" in r.stderr
    assert synthesis(env).read_text() == "previous synthesis"     # v1 truncated it


def test_prompt_file_is_never_committed(env):
    run(env, "--auto")
    assert (env["kr"] / ".distill-prompt.md").exists()
    untracked = subprocess.run(["git", "-C", str(env["kr"]), "status", "--porcelain"],
                               capture_output=True, text=True).stdout
    assert ".distill-prompt.md" not in untracked


def test_interactive_opens_claude_with_prompt(env):
    r = run(env)
    assert r.returncode == 0
    assert f"args=--append-system-prompt-file {env['kr'] / '.distill-prompt.md'}" in env["log"].read_text()


def test_auto_without_claude(env):
    r = run(env, "--auto", PATH=f"{ROOT / '.venv' / 'bin'}:/usr/bin:/bin")
    assert r.returncode != 0 and "'claude' CLI not found" in r.stderr
