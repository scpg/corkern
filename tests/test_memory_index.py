"""MEMORY.md read limit (200 lines / 25 KB): status warns, the session context asks Claude to
shorten the index — also when the extra lines came from another device."""
import json

import pytest
from typer.testing import CliRunner

from corkern.main import app
from corkern.memory_index import LIMIT_LINES, usage


def test_usage_counts_lines_and_bytes():
    assert usage("").lines == 0
    assert usage("a\nb").lines == 2 and usage("a\nb\n").lines == 2
    assert usage("x\n" * LIMIT_LINES).over is False
    assert usage("x\n" * (LIMIT_LINES + 1)).over is True
    assert usage("x\n" * 185).near is True
    assert usage("y" * 26_000).over is True


@pytest.fixture
def repo(tmp_path, monkeypatch):
    kr = tmp_path / "kr"
    project = tmp_path / "proj"
    project.mkdir()
    assert CliRunner().invoke(app, ["init", str(kr)]).exit_code == 0
    monkeypatch.setenv("CORKERN_MEMORY_DIR", str(kr))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert CliRunner().invoke(app, ["add", "proj", str(project)]).exit_code == 0
    (kr / "memory" / "proj").mkdir(parents=True, exist_ok=True)
    return kr, project


def _index(kr, lines):
    (kr / "memory" / "proj" / "MEMORY.md").write_text("".join(f"- [m{i}](m{i}.md) — x\n" for i in range(lines)))


def _context(project):
    return CliRunner().invoke(app, ["session-context"], input=json.dumps({"cwd": str(project)})).output


def test_status_and_context_quiet_when_small(repo):
    kr, project = repo
    _index(kr, 20)
    assert "proj:" not in CliRunner().invoke(app, ["status"]).output
    assert "read limit" not in _context(project)


def test_over_the_limit(repo):
    kr, project = repo
    _index(kr, 240)
    status = CliRunner().invoke(app, ["status"]).output
    assert "proj: 240 lines" in status and "NOT loaded" in status
    ctx = _context(project)
    assert "MEMORY.md OVER Claude Code's read limit [proj]" in ctx
    assert "Please shorten it now" in ctx


def test_near_the_limit(repo):
    kr, project = repo
    _index(kr, 190)
    assert "close to the limit" in CliRunner().invoke(app, ["status"]).output
    assert "MEMORY.md close to Claude Code's read limit" in _context(project)
