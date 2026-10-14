"""init-device — autoMemoryDirectory per project, plan first, never while v1 is installed."""
import json
import subprocess

import pytest
from typer.testing import CliRunner

from corkern.commands import init_device_cmd
from corkern.main import app


@pytest.fixture
def setup(tmp_path, monkeypatch):
    kr = tmp_path / "kr"
    assert CliRunner().invoke(app, ["init", str(kr)]).exit_code == 0
    monkeypatch.setenv("CORKERN_MEMORY_DIR", str(kr))
    monkeypatch.setattr(init_device_cmd, "_v1_installed", lambda: False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))   # no global git ignore file
    monkeypatch.setenv("HOME", str(tmp_path / "home"))               # Claude Code files: tmp ~/.claude
    (tmp_path / "home").mkdir()
    proj = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    plain = tmp_path / "plain"
    plain.mkdir()
    for slug, path in (("proj", proj), ("plain", plain), ("gone", tmp_path / "missing")):
        assert CliRunner().invoke(app, ["add", slug, str(path)]).exit_code == 0
    return kr, proj, plain


def run(*args, stdin=""):
    return CliRunner().invoke(app, ["init-device", *args], input=stdin)


def test_plan_changes_nothing(setup):
    kr, proj, plain = setup
    r = run()
    assert r.exit_code == 0, r.output
    assert f"autoMemoryDirectory = {kr / 'memory' / 'proj'}" in r.output
    assert "gone" in r.output and "not on this machine" in r.output
    assert "corkern init-device --apply" in r.output
    assert "SessionStart → corkern-session-start" in r.output
    assert not (proj / ".claude").exists() and not (plain / ".claude").exists()
    assert not (proj.parent / "home" / ".claude").exists()


def test_apply_writes_local_settings_and_keeps_other_keys(setup):
    kr, proj, plain = setup
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.local.json").write_text('{"permissions": {"allow": ["Bash(ls)"]}}')
    r = run("--apply", "--yes")
    assert r.exit_code == 0, r.output
    settings = json.loads((proj / ".claude" / "settings.local.json").read_text())
    assert settings["autoMemoryDirectory"] == str(kr / "memory" / "proj")
    assert settings["permissions"] == {"allow": ["Bash(ls)"]}
    assert json.loads((plain / ".claude" / "settings.local.json").read_text())["autoMemoryDirectory"] \
        == str(kr / "memory" / "plain")
    # machine-specific: excluded from the project's git, never committed
    assert ".claude/settings.local.json" in (proj / ".git" / "info" / "exclude").read_text()
    assert subprocess.run(["git", "-C", str(proj), "status", "--porcelain"],
                          capture_output=True, text=True).stdout == ""
    assert "Nothing to change" in run().output         # idempotent


def test_apply_asks_and_respects_no(setup):
    _, proj, _ = setup
    r = run("--apply", stdin="n\n")
    assert "Nothing changed" in r.output and not (proj / ".claude").exists()


def test_invalid_json_is_skipped_not_overwritten(setup):
    _, proj, _ = setup
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.local.json").write_text("{broken")
    r = run("--apply", "--yes")
    assert "not valid JSON" in r.output
    assert (proj / ".claude" / "settings.local.json").read_text() == "{broken"


def test_refused_while_v1_is_installed(setup, monkeypatch):
    _, proj, _ = setup
    monkeypatch.setattr(init_device_cmd, "_v1_installed", lambda: True)
    r = run("--apply", "--yes")
    assert r.exit_code == 1 and "v1 is installed" in r.output
    assert not (proj / ".claude").exists()


def test_home_project_wired_in_claudes_own_local_settings(setup, monkeypatch, tmp_path):
    """v1's project "home" = ~: Claude Code reads ~/.claude/settings.local.json only for sessions
    started in ~ (probed with real Claude Code 2.1.285), so it is the home project's local file."""
    kr, _, _ = setup
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert CliRunner().invoke(app, ["add", "home", str(home)]).exit_code == 0
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.local.json").write_text('{"permissions": {"allow": ["Bash(ls:*)"]}}')
    r = run("--apply", "--yes")
    assert r.exit_code == 0, r.output
    s = json.loads((home / ".claude" / "settings.local.json").read_text())
    assert s[init_device_cmd.KEY] == str(kr / "memory" / "home")
    assert s["permissions"] == {"allow": ["Bash(ls:*)"]}                 # other keys kept
    assert list((home / ".claude").glob("settings.local.json.bak-corkern-*"))
    assert "keep it out of shared dotfiles" in r.output
    assert "home" in run().output and "already set" in run().output     # idempotent


def test_home_project_not_wired_with_custom_claude_config_dir(setup, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
    assert CliRunner().invoke(app, ["add", "home", str(home)]).exit_code == 0
    r = run("--apply", "--yes")
    assert "CLAUDE_CONFIG_DIR is set" in r.output and not (home / ".claude" / "settings.local.json").exists()


# ── hooks + global CLAUDE.md in Claude Code's user files ───────────────────────────────
def _claude(tmp_path):
    return tmp_path / "home" / ".claude"


def test_apply_wires_hooks_and_keeps_other_settings(setup, tmp_path):
    kr, _, _ = setup
    c = _claude(tmp_path)
    c.mkdir()
    (c / "settings.json").write_text(json.dumps({
        "model": "opus", "env": {"FOO": "1"},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]},
                           {"hooks": [{"type": "command", "command": "bash ~/.claude/corkern.sh corkern-push"}]}]}}))
    r = run("--apply", "--yes")
    assert r.exit_code == 0, r.output
    s = json.loads((c / "settings.json").read_text())
    assert s["model"] == "opus" and s["env"] == {"FOO": "1"}          # no machine path in settings
    stop = [h["command"] for g in s["hooks"]["Stop"] for h in g["hooks"]]
    assert stop[0] == "notify-send done" and len(stop) == 2            # other hook kept
    assert stop[1].endswith('bash "$f" corkern-push')                    # old corkern hook replaced
    assert s["hooks"]["PostToolUse"][0]["matcher"] == "Write|Edit"
    for event in ("SessionStart", "PostToolUse", "Stop", "SessionEnd"):
        assert sum("corkern.sh" in h["command"] for g in s["hooks"][event] for h in g["hooks"]) == 1
    assert list(c.glob("settings.json.bak-corkern-*"))                  # previous version kept
    assert "Nothing to change" in run().output                            # idempotent


def test_global_claude_md_is_imported_not_replaced(setup, tmp_path):
    kr, _, _ = setup
    (kr / "global").mkdir(exist_ok=True)
    (kr / "global" / "CLAUDE.md").write_text("# Global\n- be kind\n")
    c = _claude(tmp_path)
    c.mkdir()
    (c / "CLAUDE.md").write_text("# Mine\n- my own rule\n")
    assert run("--apply", "--yes").exit_code == 0
    text = (c / "CLAUDE.md").read_text()
    assert text.startswith("# Mine\n- my own rule\n") and f"@{kr}/global/CLAUDE.md" in text.splitlines()
    run("--apply", "--yes")
    assert (c / "CLAUDE.md").read_text().count("@") == 1                 # not added twice


def test_symlinked_claude_md_is_left_alone(setup, tmp_path):
    kr, _, _ = setup
    (kr / "global").mkdir(exist_ok=True)
    (kr / "global" / "CLAUDE.md").write_text("x\n")
    c = _claude(tmp_path)
    c.mkdir()
    other = tmp_path / "v1-global.md"
    other.write_text("v1\n")
    (c / "CLAUDE.md").symlink_to(other)
    r = run("--apply", "--yes")
    assert "symlink" in r.output and other.read_text() == "v1\n"


def test_invalid_settings_json_is_not_touched(setup, tmp_path):
    c = _claude(tmp_path)
    c.mkdir()
    (c / "settings.json").write_text("{nope")
    r = run("--apply", "--yes")
    assert "not valid JSON" in r.output and (c / "settings.json").read_text() == "{nope"


def test_claude_config_dir_is_honoured(setup, tmp_path, monkeypatch):
    other = tmp_path / "claude-alt"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(other))
    assert run("--apply", "--yes").exit_code == 0
    assert "hooks" in json.loads((other / "settings.json").read_text())
    assert not _claude(tmp_path).exists()


def test_v1_home_settings_are_never_written(setup, tmp_path, monkeypatch):
    """A home where v1 is set up: plan says so, the guard refuses even a direct write."""
    home = tmp_path / "home"
    (home / "dev" / "corkern").mkdir(parents=True)
    (home / "dev" / "corkern" / ".corkern-v1.sentinel").write_text("")
    r = run()
    assert "belongs to corkern v1" in r.output
    from corkern import guard
    with pytest.raises(guard.V1ProtectedError):
        guard.write_claude_config("settings.json", "{}")
    with pytest.raises(ValueError):
        guard.write_claude_config("../evil.json", "{}")
    assert not (home / ".claude").exists()


def test_status_reports_hook_wiring(setup, tmp_path):
    assert "init-device --apply" in CliRunner().invoke(app, ["status"]).output
    run("--apply", "--yes")
    assert "Hooks wired in Claude Code" in CliRunner().invoke(app, ["status"]).output


def test_slash_commands_installed(setup, tmp_path):
    assert run("--apply", "--yes").exit_code == 0
    cmds = sorted(p.name for p in (_claude(tmp_path) / "commands").glob("*.md"))
    assert cmds == ["corkern-distill.md", "corkern-harvest.md", "corkern-memory-prune.md", "corkern-status.md"]
    assert "corkern harvest" in (_claude(tmp_path) / "commands" / "corkern-harvest.md").read_text()
    assert "Nothing to change" in run().output


def test_settings_keep_non_ascii_and_audit_hooks(setup, tmp_path):
    """Dotfiles under version control (yadm): no \\uXXXX churn, other hooks byte-for-byte kept."""
    _, _, _ = setup
    c = _claude(tmp_path)
    c.mkdir()
    audit = {"matcher": "*", "hooks": [{"type": "command", "command": "~/bin/audit-log.sh — prüfen"}]}
    (c / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [audit]}, "note": "Grüße"}, ensure_ascii=False))
    assert run("--apply", "--yes").exit_code == 0
    text = (c / "settings.json").read_text()
    assert "Grüße" in text and "prüfen" in text and "\\u00" not in text
    assert json.loads(text)["hooks"]["PreToolUse"] == [audit]



def test_settings_are_machine_neutral_for_shared_dotfiles(setup, tmp_path, monkeypatch):
    """yadm & co share ~/.claude between machines: $HOME paths, no-op without corkern, no env."""
    kr, _, _ = setup
    home = tmp_path / "home"
    code = home / ".local/share/corkern"
    monkeypatch.setenv("CORKERN_CODE_DIR", str(code))
    c = _claude(tmp_path)
    c.mkdir()
    (c / "settings.json").write_text(json.dumps({"env": {"CORKERN_MEMORY_DIR": str(kr)}}))  # older corkern
    assert run("--apply", "--yes").exit_code == 0
    text = (c / "settings.json").read_text()
    s = json.loads(text)
    assert str(home) not in text and "env" not in s
    cmd = s["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert cmd == 'f="$HOME/.local/share/corkern/hooks/corkern.sh"; [ -f "$f" ] || exit 0; bash "$f" corkern-push'
    # the packaged hooks were installed where settings.json points, stamped with the version
    from corkern import __version__
    assert (code / "hooks" / "VERSION").read_text().strip() == __version__
    assert (code / "hooks" / "corkern.sh").read_bytes() == \
        (init_device_cmd.hooks_install.packaged("hooks") / "corkern.sh").read_bytes()
    assert (code / "hooks" / "corkern.sh").stat().st_mode & 0o111
    ran = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert ran.returncode == 0 and ran.stdout == ""                    # push hook: silent, never fails
    other = tmp_path / "machine-without-corkern"
    other.mkdir()
    ran = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env={"HOME": str(other), "PATH": "/usr/bin:/bin"})
    assert ran.returncode == 0 and ran.stdout == "" and ran.stderr == ""


def test_global_import_uses_tilde_and_replaces_older_absolute_line(setup, tmp_path, monkeypatch):
    home = tmp_path / "home"
    kr = home / ".corkern"
    assert CliRunner().invoke(app, ["init", str(kr)]).exit_code == 0
    monkeypatch.setenv("CORKERN_MEMORY_DIR", str(kr))
    (kr / "global").mkdir(exist_ok=True)
    (kr / "global" / "CLAUDE.md").write_text("# Global\n")
    c = _claude(tmp_path)
    c.mkdir()
    (c / "CLAUDE.md").write_text(f"# Mine\n# corkern: global instructions from the knowledge repo (synced)\n@{kr}/global/CLAUDE.md\n")
    assert run("--apply", "--yes").exit_code == 0
    lines = (c / "CLAUDE.md").read_text().splitlines()
    assert "@~/.corkern/global/CLAUDE.md" in lines and f"@{kr}/global/CLAUDE.md" not in lines
    assert lines[0] == "# Mine" and sum(x.startswith("@") for x in lines) == 1
    assert "Nothing to change" in run().output or "already set" in run().output



def test_hooks_install_states_and_update(tmp_path, monkeypatch):
    from corkern import __version__, hooks_install
    monkeypatch.setenv("CORKERN_CODE_DIR", str(tmp_path / "share"))
    assert hooks_install.state() == "missing"
    hooks_install.install()
    assert hooks_install.state() == "current"
    (tmp_path / "share" / "hooks" / "VERSION").write_text("1.9.0\n")
    assert hooks_install.state() == "outdated"
    hooks_install.install()
    assert hooks_install.installed_version() == __version__


def test_hooks_install_leaves_an_old_git_clone_alone(tmp_path, monkeypatch):
    from corkern import hooks_install
    share = tmp_path / "share"
    (share / ".git").mkdir(parents=True)
    (share / "hooks").mkdir()
    (share / "hooks" / "corkern.sh").write_text("old clone\n")
    monkeypatch.setenv("CORKERN_CODE_DIR", str(share))
    assert hooks_install.state() == "git-clone"
    with pytest.raises(hooks_install.HooksError, match="git-clone"):
        hooks_install.install()
    assert (share / "hooks" / "corkern.sh").read_text() == "old clone\n"
