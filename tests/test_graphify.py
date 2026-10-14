"""corkern graphify — run against a stub `graphify` that records how it was called."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
C2 = ROOT / ".venv" / "bin" / "corkern"

STUB = """#!/usr/bin/env bash
echo "args=$* cwd=$PWD key=${ANTHROPIC_API_KEY:+set}${OPENAI_API_KEY:+openai-set}" >> "$GRAPHIFY_LOG"
case "$1" in
  claude|hook) exit 0 ;;
  cluster-only) exit 0 ;;
  *) mkdir -p graphify-out && echo '{}' > graphify-out/graph.json; exit "${GRAPHIFY_RC:-0}" ;;
esac
"""


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    proj = home / "dev" / "proj"
    proj.mkdir(parents=True)
    kr = tmp_path / "kr"
    subprocess.run([str(C2), "init", str(kr)], check=True, capture_output=True,
                   env={**os.environ, "HOME": str(home)})
    (kr / "corkern.conf").write_text("proj:dev/proj\nhome:\ngone:dev/gone\n")
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    (stub_dir / "graphify").write_text(STUB)
    (stub_dir / "graphify").chmod(0o755)
    log = tmp_path / "graphify.log"
    e = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    e.update(HOME=str(home), CORKERN_MEMORY_DIR=str(kr), GRAPHIFY_LOG=str(log),
             PATH=f"{stub_dir}:{e['PATH']}")
    return {"env": e, "home": home, "proj": proj, "kr": kr, "log": log}


def run(env, *args, cwd=None, **extra):
    return subprocess.run([str(C2), "graphify", *args], cwd=cwd or env["home"],
                          env={**env["env"], **extra}, capture_output=True, text=True, timeout=60)


def calls(env) -> list[str]:
    return env["log"].read_text().splitlines() if env["log"].exists() else []


def test_no_key_builds_code_only_graph(env):
    r = run(env, "proj")
    assert r.returncode == 0, r.stderr
    assert "code-only graph" in r.stdout
    assert calls(env)[0].startswith(f"args=update . cwd={env['proj']}")
    assert calls(env)[1].startswith("args=cluster-only . --no-label")


def test_key_from_environment_uses_backend(env):
    run(env, "proj", ANTHROPIC_API_KEY="sk-test-secret")
    assert calls(env)[0].startswith("args=. --backend=claude") and "key=set" in calls(env)[0]


def test_existing_graph_is_incremental_unless_full(env):
    (env["proj"] / "graphify-out").mkdir()
    (env["proj"] / "graphify-out" / "graph.json").write_text("{}")
    run(env, "proj", ANTHROPIC_API_KEY="x")
    run(env, "proj", "--full", ANTHROPIC_API_KEY="x")
    builds = [c for c in calls(env) if not c.startswith("args=cluster-only")]
    assert builds[0].startswith("args=. --update --backend=claude")
    assert builds[1].startswith("args=. --backend=claude")


def test_key_from_project_dotenv_is_passed_but_never_printed(env):
    (env["proj"] / ".env").write_text("# secrets\nOPENAI_API_KEY='sk-openai-secret'\nANTHROPIC_API_KEY=sk-ant-secret\n")
    r = run(env, "proj")
    assert "ANTHROPIC_API_KEY (project .env)" in r.stdout          # preference order like v1
    assert calls(env)[0].startswith("args=. --backend=claude") and "key=set" in calls(env)[0]
    assert "sk-ant-secret" not in r.stdout + r.stderr
    assert "openai-set" not in calls(env)[0]                      # only ONE key is passed


def test_slug_from_cwd(env):
    run(env, cwd=env["proj"])
    assert calls(env)[0].startswith(f"args=update . cwd={env['proj']}")


@pytest.mark.parametrize("args,needle", [
    ([], "Could not detect project slug"),
    (["nope"], "not found in corkern.conf"),
    (["gone"], "Project directory not found"),
])
def test_errors(env, args, needle):
    r = run(env, *args, cwd=env["kr"])        # not a project dir (home itself is the `home` project)
    assert r.returncode != 0 and needle in r.stderr
    assert calls(env) == []


def test_not_installed(env):
    e = {**env["env"], "PATH": f"{ROOT / '.venv' / 'bin'}:/usr/bin:/bin"}
    r = subprocess.run([str(C2), "graphify", "proj"], env=e, capture_output=True, text=True)
    assert r.returncode != 0 and "uv tool install graphifyy" in r.stderr


def test_install_registers_in_project(env):
    r = run(env, "proj", "--install")
    assert r.returncode == 0, r.stderr
    assert [c.split(" cwd=")[0] for c in calls(env)] == ["args=claude install", "args=hook install"]


def test_install_refused_for_home_project(env):
    r = run(env, "home", "--install")
    assert r.returncode != 0 and "Refusing --install" in r.stderr
    assert calls(env) == []


def test_project_inside_v1_refused(env):
    (env["proj"] / ".corkern-v1.sentinel").touch()
    r = run(env, "proj")
    assert r.returncode != 0 and "v1-protected" in r.stderr
    assert calls(env) == []


def test_failed_build_reports_exit_code(env):
    r = run(env, "proj", GRAPHIFY_RC="3")
    assert r.returncode != 0 and "exited with code 3" in r.stderr


def test_guard_allows_only_listed_tools(tmp_path):
    from corkern import guard
    with pytest.raises(ValueError):
        guard.run_tool(tmp_path, ["rm", "-rf", "x"])
