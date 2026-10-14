"""Symlinks committed into a shared knowledge repo are never followed (audit C1 H1 H2 H4 M9 L9).

Threat: a teammate or a compromised device with push access commits SYMLINKS into the knowledge
repo. corkern must not read, write or move files THROUGH them — outside the repo. Every target
here lives in a scratch HOME; the real one is never touched.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corkern import config, guard
from corkern.commands import distill_cmd, harvest_cmd, init_device_cmd
from corkern.config import MemoryRepo, load_config
from corkern.main import app

ORIGINAL = "alias ll='ls -l'\n"
SECRET = "ghp_" + "A" * 36


def run(*args, stdin=""):
    return CliRunner().invoke(app, list(args), input=stdin)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CORKERN_DEVICE_NAME", "laptop")
    for var in ("CLAUDE_CONFIG_DIR", "CORKERN_CODE_DIR", "CORKERN"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def kr(tmp_path, home, monkeypatch) -> Path:
    kr = tmp_path / "kr"
    assert run("init", str(kr)).exit_code == 0
    monkeypatch.setenv("CORKERN_MEMORY_DIR", str(kr))
    (tmp_path / "web").mkdir()
    assert run("add", "web", str(tmp_path / "web")).exit_code == 0
    (kr / "memory" / "web").mkdir(parents=True, exist_ok=True)
    return kr


def plant(link: Path, target: Path, content: str = ORIGINAL) -> Path:
    """`target` (outside the repo) with `content`, and `link` → target inside the repo."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)
    return target


def commit(kr: Path) -> None:
    subprocess.run(["git", "-C", str(kr), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(kr), "commit", "-qm", "teammate push"], check=True, capture_output=True)


def session_start(kr: Path):
    return run("session-context", stdin=json.dumps({"cwd": str(kr.parent / "web")}))


# ── the reproduced attacks ───────────────────────────────────────────────────────────────
def test_c1_device_manifest_symlink_is_replaced_not_followed(kr, home):
    aliases = plant(kr / ".devices" / "laptop", home / ".bash_aliases")
    with (kr / "corkern.conf").open("a") as f:
        f.write("zz:/x/$(touch PWNED)\n")
    r = session_start(kr)
    assert r.exit_code == 0, r.output
    assert aliases.read_text() == ORIGINAL
    manifest = kr / ".devices" / "laptop"
    assert not manifest.is_symlink() and "web:" in manifest.read_text()
    assert "$(" not in manifest.read_text()


def test_c1_metrics_symlink_not_followed(kr, home):
    profile = plant(kr / ".metrics.tsv", home / ".profile")
    r = session_start(kr)
    assert r.exit_code == 0, r.output
    assert profile.read_text() == ORIGINAL


def test_h1_distill_prompt_never_written_through_symlink(kr, home, monkeypatch):
    bashrc = plant(kr / ".distill-prompt.md", home / ".bashrc")
    monkeypatch.setattr(guard, "which", lambda tool: None)      # no claude: prompt is only saved
    r = run("distill")
    assert r.exit_code == 0, r.output
    assert bashrc.read_text() == ORIGINAL
    saved = config.state_dir() / distill_cmd.PROMPT_NAME
    assert saved.is_file() and saved.stat().st_mode & 0o777 == 0o600


def test_h2_memory_prune_does_not_move_symlink_target_into_repo(kr, home):
    hosts = plant(kr / "memory" / "web" / "old.md", home / ".config" / "gh" / "hosts.yml",
                  f"---\nstatus: stale\n---\noauth_token: {SECRET}\n")
    commit(kr)
    run("memory-prune", "web", "--yes")
    assert hosts.is_file() and SECRET in hosts.read_text()
    archive = kr / "memory" / "web" / "archive"
    assert not archive.exists() or not any(archive.iterdir())


def test_h2_refine_does_not_move_symlink_target_into_repo(kr, home, monkeypatch):
    hosts = plant(kr / "memory" / "web" / "harvested-x.md", home / ".config" / "gh" / "hosts.yml",
                  f"oauth_token: {SECRET}\n")
    commit(kr)
    monkeypatch.setattr(harvest_cmd, "_claude_or_fail", lambda: None)
    monkeypatch.setattr(harvest_cmd, "_run_claude", lambda *a, **k: "NO_MEMORIES\n")
    r = run("refine", "web")
    assert hosts.is_file() and SECRET in hosts.read_text()
    assert "nothing to refine" in r.output


def test_h4_init_device_never_imports_symlinked_global_claude_md(kr, home):
    plant(kr / "global" / "CLAUDE.md", home / ".ssh" / "id_ed25519",
          "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n")
    commit(kr)
    load_config()
    plan = init_device_cmd._claude_md_plan(kr)
    assert plan.action == "skip" and "symlink" in plan.note


def test_m9_distill_prompt_never_reads_symlinked_memory(kr, home):
    plant(kr / "memory" / "global" / "notes.md", home / ".aws" / "credentials",
          f"aws_secret_access_key = {SECRET}\n")
    repo, _ = load_config()
    prompt, _ = distill_cmd.build_prompt(repo)
    assert SECRET not in prompt


def test_l9_secrets_redact_never_rewrites_symlink_target(kr, home):
    notes = plant(kr / "memory" / "web" / "leak.md", home / "notes.txt", f"token {SECRET}\n")
    r = run("secrets", "--redact", str(kr / "memory" / "web" / "leak.md"))
    assert notes.read_text() == f"token {SECRET}\n"
    assert "HOLD memory/web/leak.md" in r.output


def test_status_and_session_context_warn_about_tracked_symlinks(kr, home):
    plant(kr / "memory" / "web" / "linked.md", home / "anything.md")
    commit(kr)
    status = run("status")
    assert "symlink" in status.output and "memory/web/linked.md" in status.output
    context = session_start(kr)
    assert context.exit_code == 0, context.output
    assert "symlink" in context.output and "memory/web/linked.md" in context.output


# ── a symlinked SHARED file is absent + a warning, never an error (no team-wide DoS) ──────
def test_symlinked_corkern_conf_is_absent_and_warned_everywhere(kr, home):
    passwd = plant(kr / "corkern.conf", home / "elsewhere.conf", "evil:/x/$(id)\n")
    commit(kr)
    listing = run("list")
    assert listing.exit_code == 0, listing.output
    assert "evil" not in listing.output
    status = run("status")
    assert "Traceback" not in status.output and "corkern.conf" in status.output and "symlink" in status.output
    context = session_start(kr)
    assert context.exit_code == 0, context.output
    assert "symlink" in context.output and "corkern.conf" in context.output
    assert passwd.read_text() == "evil:/x/$(id)\n"


@pytest.mark.parametrize("rel", [".metrics.tsv", ".devices/laptop", "global/CLAUDE.md",
                                 "memory/web/MEMORY.md", "memory/web/todo.md", ".corkern-format",
                                 "memory"])
def test_symlinked_shared_file_never_breaks_session_start(kr, home, rel):
    target = home / "target"
    if rel == "memory":
        target.mkdir()
        (kr / "memory").rename(kr.parent / "memory-moved")
        (kr / "memory").symlink_to(target)
    else:
        plant(kr / rel, target, "- [ ] x\n")
    commit(kr)
    context = session_start(kr)
    assert context.exit_code == 0, context.output
    if rel == ".devices/laptop":           # this device's own manifest: the link is replaced
        manifest = kr / rel
        assert not manifest.is_symlink() and "web:" in manifest.read_text()
        assert target.read_text() == "- [ ] x\n"
        return
    assert "symlink" in context.output and f"    {rel}\n" in context.output
    status = run("status")
    assert "Traceback" not in status.output and f"    {rel}" in status.output
    if target.is_file():
        assert target.read_text() == "- [ ] x\n"


def test_explicit_action_on_a_symlink_is_a_clean_refusal(kr, home):
    plant(kr / "memory" / "web" / "todo.md", home / "todo-elsewhere.md", "- [ ] x\n")
    result = subprocess.run([str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "corkern"),
                             "remind", "do it", "web"], capture_output=True, text=True,
                            env={**os.environ}, timeout=60)
    assert result.returncode != 0
    assert "symlink" in result.stderr and "Traceback" not in result.stderr
    assert (home / "todo-elsewhere.md").read_text() == "- [ ] x\n"


# ── corkern.conf: project paths that are shell code ───────────────────────────────────────
def test_conf_skips_project_paths_with_shell_metacharacters(capsys):
    text = ("a:/x/$(touch P)\nb:/x/`id`\nc:/x;rm -rf ~\nd:/srv/my projects/app\ne:/x|y\n"
            "f:/x&y\ng:/x<y\nh:/x>y\ni:/x\\y\nj:/x\x01y\nk:dev/plain-path_1.2\n")
    entries = config._parse_conf(text, "corkern.conf")
    assert set(entries) == {"d", "k"}
    assert entries["d"] == Path("/srv/my projects/app")
    err = capsys.readouterr().err
    assert all(f"{slug!r}" in err for slug in "abcefghij")


def test_conf_windows_paths_stay_silent(capsys):
    assert config._parse_conf("w:C:\\dev\\x\n", "corkern.conf") == {}
    assert capsys.readouterr().err == ""


# ── the guard rule itself ────────────────────────────────────────────────────────────────
@pytest.fixture
def root(tmp_path) -> Path:
    root = tmp_path / "kr"
    (root / "memory" / "web").mkdir(parents=True)
    (root / "corkern.conf").write_text("")
    MemoryRepo(root)
    return root


@pytest.fixture
def outside(tmp_path) -> Path:
    d = tmp_path / "outside"
    d.mkdir()
    (d / "file").write_text(ORIGINAL)
    return d


class TestGuardRepoSymlinks:
    def test_symlink_as_last_component_refused(self, root, outside):
        (root / "memory" / "web" / "a.md").symlink_to(outside / "file")
        with pytest.raises(guard.V1ProtectedError, match="symlink"):
            guard.read_text(root / "memory" / "web" / "a.md")
        with pytest.raises(guard.V1ProtectedError, match="symlink"):
            guard.write_text(root / "memory" / "web" / "a.md", "x")
        assert (outside / "file").read_text() == ORIGINAL

    def test_symlink_as_intermediate_directory_refused(self, root, outside):
        (root / "memory" / "linked").symlink_to(outside)
        with pytest.raises(guard.V1ProtectedError, match="symlink"):
            guard.write_text(root / "memory" / "linked" / "new.md", "x")
        assert not (outside / "new.md").exists()

    def test_symlink_inside_repo_pointing_inside_also_refused(self, root):
        (root / "memory" / "web" / "real.md").write_text("x")
        (root / "memory" / "web" / "alias.md").symlink_to(root / "memory" / "web" / "real.md")
        with pytest.raises(guard.V1ProtectedError, match="symlink"):
            guard.assert_safe(root / "memory" / "web" / "alias.md")

    def test_resolved_outside_root_refused(self, root, outside):
        (root / "global").symlink_to(outside)
        with pytest.raises(guard.V1ProtectedError, match="symlink"):
            guard.assert_safe(root / "global" / "file")

    def test_resolved_spelling_of_root_checked_too(self, tmp_path, outside):
        real = tmp_path / "real"
        (real / "memory").mkdir(parents=True)
        (real / "corkern.conf").write_text("")
        link = tmp_path / "kr-link"
        link.symlink_to(real)
        MemoryRepo(link)
        (real / "memory" / "a.md").symlink_to(outside / "file")
        for spelling in (link, real):
            with pytest.raises(guard.V1ProtectedError, match="symlink"):
                guard.read_text(spelling / "memory" / "a.md")

    def test_root_itself_may_be_a_symlink(self, tmp_path):
        real = tmp_path / "data" / "kr"
        (real / "memory").mkdir(parents=True)
        (real / "corkern.conf").write_text("")
        link = tmp_path / "home-corkern"
        link.symlink_to(real)
        repo = MemoryRepo(link)
        repo.write("web", "a.md", "hello")
        assert repo.read("web", "a.md") == "hello"
        guard.write_text(link / "memory" / "web" / "b.md", "x")
        assert (real / "memory" / "web" / "b.md").read_text() == "x"

    def test_paths_outside_the_repo_keep_following_symlinks(self, root, tmp_path, outside):
        link = tmp_path / "elsewhere"
        link.symlink_to(outside)
        guard.write_text(link / "new", "ok")
        assert (outside / "new").read_text() == "ok"

    @pytest.mark.parametrize("write", [
        lambda p: guard.write_text(p, "x"),
        lambda p: guard.append_text(p, "x"),
        lambda p: guard.write_bytes(p, b"x"),
        lambda p: guard.write_private(p, "x"),
    ])
    def test_writes_open_with_o_nofollow(self, root, outside, monkeypatch, write):
        """A link swapped in after the check (TOCTOU) is still not followed."""
        link = root / "memory" / "web" / "late.md"
        link.symlink_to(outside / "file")
        monkeypatch.setattr(guard, "assert_safe", lambda p: Path(os.path.abspath(p)))
        with pytest.raises(OSError):
            write(link)
        assert (outside / "file").read_text() == ORIGINAL

    def test_walk_and_list_skip_symlinks(self, root, outside):
        mem = root / "memory" / "web"
        (mem / "real.md").write_text("x")
        (mem / "file.md").symlink_to(outside / "file")
        (mem / "dir").symlink_to(outside)
        (outside / "deep.md").write_text("x")
        assert [f.name for f in guard.walk_files(root / "memory", ".md")] == ["real.md"]
        assert [n for n, _ in guard.list_files(mem, ".md")] == ["real.md"]
        assert guard.list_dirs(root / "memory") == ["web"]

    def test_move_refuses_symlinked_source_and_destination(self, root, outside):
        mem = root / "memory" / "web"
        (mem / "link.md").symlink_to(outside / "file")
        with pytest.raises(guard.V1ProtectedError, match="symlink"):
            guard.move(mem / "link.md", mem / "archive" / "x.md")
        assert (outside / "file").read_text() == ORIGINAL
        (mem / "real.md").write_text("x")
        (mem / "dangling.md").symlink_to(outside / "missing")
        with pytest.raises((guard.V1ProtectedError, FileExistsError)):
            guard.move(mem / "real.md", mem / "dangling.md")
        assert not (outside / "missing").exists()

    def test_remove_symlink_only_removes_the_link(self, root, outside):
        link = root / ".devices" / "laptop"
        link.parent.mkdir()
        link.symlink_to(outside / "file")
        guard.remove_symlink(link)
        assert not link.is_symlink() and (outside / "file").read_text() == ORIGINAL
