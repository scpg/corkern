"""corkern iwe — IWE over the knowledge repo as an MCP server (stubs for iwe/iwec/claude here;
the real binaries and real Claude Code are exercised in docker/claude)."""
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corkern import guard
from corkern.main import app

ROOT = Path(__file__).resolve().parents[1]
STUB = '#!/usr/bin/env bash\necho "$(basename "$0") $*" >> "$STUB_LOG"\n'
CLAUDE = STUB + 'if [ "$1 $2" = "mcp get" ] && [ -f "$STUB_DIR/registered" ]; then echo "  Command: $(cat "$STUB_DIR/registered")"; exit 0; fi\n' \
                'if [ "$1 $2" = "mcp get" ]; then exit 1; fi\n'
IWE = STUB + 'if [ "$1" = init ]; then mkdir -p .iwe && echo "pwd=$PWD" > .iwe/config.toml; fi\n'
IWEC = '#!/usr/bin/env bash\necho "iwec cwd=$PWD args=$*"\n'


@pytest.fixture
def env(tmp_path, monkeypatch):
    kr = tmp_path / "kr"
    assert CliRunner().invoke(app, ["init", str(kr)]).exit_code == 0
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, body in (("claude", CLAUDE), ("iwe", IWE), ("iwec", IWEC)):
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    log = tmp_path / "stub.log"
    for k, v in {"CORKERN_MEMORY_DIR": str(kr), "STUB_LOG": str(log), "STUB_DIR": str(stubs),
                 "PATH": f"{stubs}:{os.environ['PATH']}", "HOME": str(tmp_path / "home")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(guard, "v1_installed", lambda: False)
    return kr, stubs, log


def run(*args, stdin=""):
    return CliRunner().invoke(app, ["iwe", *args], input=stdin)


def test_status_says_what_is_missing(env):
    out = run().output
    assert "✓ iwe" in out and "corkern iwe --setup" in out and "corkern iwe --install" in out


def test_setup_creates_config_in_the_knowledge_repo(env):
    kr, _, log = env
    r = run("--setup")
    assert r.exit_code == 0, r.output
    assert (kr / ".iwe" / "config.toml").read_text().strip() == f"pwd={kr}"
    assert "iwe init --auto --library memory" in log.read_text()


def test_install_needs_config_then_registers_with_env(env):
    kr, stubs, log = env
    r = run("--install", "--yes")
    assert r.exit_code == 1 and "corkern iwe --setup" in r.output
    run("--setup")
    r = run("--install", "--yes")
    assert r.exit_code == 0, r.output
    calls = log.read_text()
    assert f"claude mcp add iwe --scope user -e CORKERN_MEMORY_DIR={kr} -- " in calls and "iwe-serve" in calls
    assert "mcp remove" not in calls


def test_install_replaces_an_existing_server(env):
    _, stubs, log = env
    run("--setup")
    (stubs / "registered").write_text("/old/v1/scripts/iwec-corkern.sh")
    assert "replaces the existing 'iwe' server" in run("--install", "--yes").output
    assert "claude mcp remove iwe --scope user" in log.read_text()


def test_install_asks_and_shows_the_manual_route(env):
    run("--setup")
    r = run("--install", stdin="n\n")
    assert "Nothing changed" in r.output and "claude mcp add iwe --scope user" in r.output


def test_install_refused_while_v1_is_installed(env, monkeypatch):
    _, _, log = env
    run("--setup")
    monkeypatch.setattr(guard, "v1_installed", lambda: True)
    r = run("--install", "--yes")
    assert r.exit_code == 1 and "v1" in r.output and "mcp add" not in log.read_text()


def test_serve_runs_iwec_inside_the_knowledge_repo(env, tmp_path):
    kr, stubs, _ = env
    r = subprocess.run([str(ROOT / ".venv/bin/corkern"), "iwe-serve"], capture_output=True, text=True,
                       cwd=tmp_path, env=os.environ.copy(), timeout=30)
    assert r.stdout.strip() == f"iwec cwd={kr} args="
