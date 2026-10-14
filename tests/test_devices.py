"""Device manifests (.devices/<device>) and `list` across devices."""
import json

import pytest
from typer.testing import CliRunner

from corkern import devices
from corkern.commands import init_device_cmd
from corkern.main import app
from tests.conftest import list_row


def run(*args, stdin=""):
    return CliRunner().invoke(app, list(args), input=stdin)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    kr = tmp_path / "kr"
    assert run("init", str(kr)).exit_code == 0
    monkeypatch.setenv("CORKERN_MEMORY_DIR", str(kr))
    monkeypatch.setenv("CORKERN_DEVICE_NAME", "laptop")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    # init-device --apply writes Claude Code's user files: never the real ones of this machine
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for var in ("CLAUDE_CONFIG_DIR", "CORKERN_CODE_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(init_device_cmd, "_v1_installed", lambda: False)
    for name in ("web", "api"):
        (tmp_path / name).mkdir()
        assert run("add", name, str(tmp_path / name)).exit_code == 0
    return kr, tmp_path


def test_parse_reads_v1_and_old_v1_formats():
    new = devices.parse("# x\nhost:h\nweb:2026-10-01 09:08:07:/a/b:c\n")
    assert new == {"web": ("2026-10-01 09:08:07", "/a/b:c")}
    old = devices.parse("web:/srv/web\nupdated:2026-01-02 03:04:05\n")
    assert old == {"web": ("2026-01-02 03:04:05", "/srv/web")}


def test_session_start_writes_and_stamps_this_device(repo):
    kr, tmp = repo
    run("session-context", stdin=json.dumps({"cwd": str(tmp / "web")}))
    first = devices.parse((kr / ".devices" / "laptop").read_text())
    assert set(first) == {"web", "api"} and first["web"][1] == str(tmp / "web")
    (kr / ".devices" / "laptop").write_text(
        (kr / ".devices" / "laptop").read_text().replace(first["api"][0], "2020-01-01 00:00:00"))
    run("session-context", stdin=json.dumps({"cwd": str(tmp / "web")}))
    again = devices.parse((kr / ".devices" / "laptop").read_text())
    assert again["api"][0] == "2020-01-01 00:00:00"        # other projects keep their stamp
    assert again["web"][0] >= first["web"][0]              # the current one is stamped


def test_list_shows_other_devices_and_wiring_status(repo):
    kr, tmp = repo
    (kr / ".devices" / "desktop").write_text("host:desktop\nweb:2026-09-01 10:00:00:/home/me/web\n")
    out = run("list").output
    assert "not wired" in list_row(out, "web")
    assert any(line.split()[:2] == ["web", "desktop"] and "registered" in line for line in out.splitlines())
    assert "allowed_signers" not in out
    assert run("init-device", "--apply", "--yes").exit_code == 0
    assert "synced" in list_row(run("list").output, "web")
    only = run("list", "-c", "SLUG,STATUS", "-s", "SLUG").output.splitlines()
    assert only[2].split() == ["SLUG", "STATUS"] and only[4].split()[0] == "api"


def test_init_device_writes_this_devices_manifest(repo):
    kr, tmp = repo
    assert run("init-device", "--apply", "--yes").exit_code == 0
    assert set(devices.parse((kr / ".devices" / "laptop").read_text())) == {"web", "api"}
    assert (tmp / "home" / ".claude" / "settings.json").is_file()      # the test's home, not the real one
