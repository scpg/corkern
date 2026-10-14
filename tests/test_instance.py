"""One corkern per machine: a copy that is not a tool install refuses to act; doctor finds every copy."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from corkern import instance

ROOT = Path(__file__).resolve().parents[1]
VENV_CLI = ROOT / ".venv" / "bin" / "corkern"


def _run(*args, dev=False, **env):
    e = {k: v for k, v in os.environ.items() if k != "CORKERN_DEV"}
    if dev:
        e["CORKERN_DEV"] = "1"
    e.update(env)
    return subprocess.run([str(VENV_CLI), *args], capture_output=True, text=True, env=e, timeout=60)


@pytest.mark.skipif(not VENV_CLI.exists(), reason="no development .venv")
class TestRefusal:
    def test_a_project_venv_copy_refuses_to_act(self, tmp_path):
        r = _run("list", HOME=str(tmp_path))
        assert r.returncode == 1 and "refuses to act" in r.stderr and "uv tool install corkern" in r.stderr

    def test_version_and_help_still_work(self, tmp_path):
        assert _run("--version", HOME=str(tmp_path)).returncode == 0
        assert _run("--help", HOME=str(tmp_path)).returncode == 0

    def test_doctor_runs_and_names_the_problem(self, tmp_path):
        r = _run("doctor", HOME=str(tmp_path), PATH="/usr/bin:/bin", XDG_CONFIG_HOME=str(tmp_path / "cfg"))
        assert r.returncode == 1 and "not a tool install" in r.stdout

    def test_setup_cannot_wire_anything_from_a_venv(self, tmp_path):
        r = _run("setup", "--new", HOME=str(tmp_path))
        assert r.returncode == 1 and not (tmp_path / ".corkern").exists()

    def test_dev_checkout_may_act(self, tmp_path):
        assert _run("--version", dev=True).returncode == 0


def _fake_env(root: Path, marker: str | None, editable=False) -> Path:
    """A python environment with a corkern console script; marker = uv | pipx | None."""
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "python").symlink_to(sys.executable)
    script = root / "bin" / "corkern"
    script.write_text(f"#!{root}/bin/python\nimport sys\n")
    script.chmod(0o755)
    if marker == "uv":
        (root / "uv-receipt.toml").write_text('[tool]\nrequirements = [{ name = "corkern"'
                                              + (', editable = "/src"' if editable else "") + " }]\n")
    elif marker == "pipx":
        (root / "pipx_metadata.json").write_text("{}")
    return root


def test_kind_detection(tmp_path):
    assert instance._kind(_fake_env(tmp_path / "a", "uv")) == "uv-tool"
    assert instance._kind(_fake_env(tmp_path / "b", "uv", editable=True)) == "uv-tool-editable"
    assert instance._kind(_fake_env(tmp_path / "c", "pipx")) == "pipx"
    assert instance._kind(_fake_env(tmp_path / "d", None)) == "other"


def test_two_installations_are_a_problem(tmp_path, monkeypatch):
    a, b = _fake_env(tmp_path / "uv", "uv"), _fake_env(tmp_path / "pipx", "pipx")
    monkeypatch.setenv("PATH", f"{a / 'bin'}:{b / 'bin'}:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    inv = instance.inventory()
    assert len(inv.copies) == 2
    assert any("2 corkern installations" in p for p in inv.problems)


def test_a_venv_copy_on_path_is_a_problem(tmp_path, monkeypatch):
    v = _fake_env(tmp_path / "proj-venv", None)
    monkeypatch.setenv("PATH", f"{v / 'bin'}:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    inv = instance.inventory()
    assert any("non-tool environment" in p for p in inv.problems)


def test_v1_and_old_wrapper_are_reported(tmp_path, monkeypatch):
    home = tmp_path / "home"
    v1 = home / "dev" / "corkern"
    v1.mkdir(parents=True)
    (v1 / "projects.conf").write_text("x:y\n")
    (v1 / "corkern").write_text("#!/bin/bash\n")
    (v1 / "corkern").chmod(0o755)
    bin_ = home / ".local" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "corkern").symlink_to(v1 / "corkern")
    (bin_ / "corkern2").write_text("#!/bin/sh\nCORKERN_MACHINE_REPO='/x'\nexec /y \"$@\"\n")
    (bin_ / "corkern2").chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    inv = instance.inventory()
    assert any("v1 command" in line for line in inv.v1) and any("v1 repo" in line for line in inv.v1)
    assert inv.old_wrapper == bin_ / "corkern2"
    assert any("v1 is still installed" in w for w in inv.warnings)
    assert any("old install.sh wrapper" in w for w in inv.warnings)
