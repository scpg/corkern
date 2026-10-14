"""corkern crg — with a stub code-review-graph that records its call."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
C2 = ROOT / ".venv" / "bin" / "corkern"
STUB = """#!/usr/bin/env bash
echo "args=$* cwd=$PWD" >> "$CRG_LOG"
[ "$1" = build ] && mkdir -p .code-review-graph
exit "${CRG_RC:-0}"
"""


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    proj = home / "dev" / "proj"
    proj.mkdir(parents=True)
    kr = tmp_path / "kr"
    subprocess.run([str(C2), "init", str(kr)], check=True, capture_output=True)
    (kr / "corkern.conf").write_text("proj:dev/proj\nhome:\n")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "code-review-graph").write_text(STUB)
    (stub / "code-review-graph").chmod(0o755)
    log = tmp_path / "crg.log"
    e = {**os.environ, "HOME": str(home), "CORKERN_MEMORY_DIR": str(kr), "CRG_LOG": str(log),
         "PATH": f"{stub}:{os.environ['PATH']}"}
    return {"env": e, "proj": proj, "log": log, "kr": kr}


def run(env, *args, **extra):
    return subprocess.run([str(C2), "crg", *args], cwd=env["kr"], env={**env["env"], **extra},
                          capture_output=True, text=True)


def test_build(env):
    r = run(env, "proj")
    assert r.returncode == 0, r.stderr
    assert env["log"].read_text().startswith(f"args=build --repo {env['proj']} cwd={env['proj']}")
    assert "crg proj --install" in r.stdout


def test_install(env):
    assert run(env, "proj", "--install").returncode == 0
    assert env["log"].read_text().startswith("args=install")


def test_install_refused_for_home(env):
    r = run(env, "home", "--install")
    assert r.returncode != 0 and "Refusing --install" in r.stderr and not env["log"].exists()


def test_v1_project_refused(env):
    (env["proj"] / ".corkern-v1.sentinel").touch()
    r = run(env, "proj")
    assert r.returncode != 0 and "v1-protected" in r.stderr and not env["log"].exists()


def test_build_failure_reported(env):
    r = run(env, "proj", CRG_RC="2")
    assert r.returncode != 0 and "exited with code 2" in r.stderr


def test_not_installed(env):
    r = run(env, "proj", PATH=f"{ROOT / '.venv' / 'bin'}:/usr/bin:/bin")
    assert r.returncode != 0 and "pipx install code-review-graph" in r.stderr
