"""status -v — full diagnostics (v1: status -v → doctor.sh)."""
import os
import time

import pytest
from typer.testing import CliRunner

from corkern import guard
from corkern.commands import init_device_cmd
from corkern.main import app


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    kr = tmp_path / "kr"
    assert CliRunner().invoke(app, ["init", str(kr)]).exit_code == 0
    for k, v in {"HOME": str(home), "CORKERN_MEMORY_DIR": str(kr), "XDG_STATE_HOME": str(tmp_path / "st"),
                 "XDG_CONFIG_HOME": str(tmp_path / "xdg")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(init_device_cmd, "_v1_installed", lambda: False)
    proj = home / "dev" / "web"
    proj.mkdir(parents=True)
    assert CliRunner().invoke(app, ["add", "web", str(proj)]).exit_code == 0
    return home, kr, proj


def status(*args):
    return CliRunner().invoke(app, ["status", *args]).output


def test_quick_status_points_to_verbose(env):
    out = status()
    assert "Full diagnostics: corkern status -v" in out and "Project wiring" not in out


def test_verbose_reports_wiring_commands_and_graphs(env):
    out = status("-v")
    assert "web: Claude Code still uses its own memory folder — run: corkern init-device --apply" in out
    assert "slash commands missing" in out
    assert "without a knowledge graph: web" in out
    assert "problem(s)" in out and "warning(s)" in out
    CliRunner().invoke(app, ["init-device", "--apply", "--yes"])
    out = status("-v")
    assert "  ✓ web" in out and "slash commands: /corkern-distill" in out


def test_verbose_finds_unsynced_claude_memory(env):
    home, _, _ = env
    mem = home / ".claude" / "projects" / "-home-x-old-project" / "memory"
    mem.mkdir(parents=True)
    (mem / "note.md").write_text("x")
    assert "-home-x-old-project: 1 file(s) in Claude Code's own memory folder" in status("-v")


def test_verbose_flags_stale_graph(env):
    _, _, proj = env
    graph = proj / "graphify-out" / "graph.json"
    graph.parent.mkdir()
    graph.write_text("{}")
    old = time.time() - 40 * 86400
    os.utime(graph, (old, old))
    assert "rebuild: corkern graphify web" in status("-v")
