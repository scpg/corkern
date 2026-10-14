"""install.sh — run end-to-end in a sandbox: fake HOME, private uv tool dirs, local code repo.

Nothing here touches the real home, ~/.claude, GitHub, or the v1 repo.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corkern.main import app

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
REAL_UV_CACHE = os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv")

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def code_repo(tmp_path_factory) -> Path:
    """A git repo holding the current working tree's package (uncommitted changes included)."""
    repo = tmp_path_factory.mktemp("code")
    for f in ("pyproject.toml", "PYPI.md", "LICENSE"):          # what the package build reads
        shutil.copy(ROOT / f, repo)
    shutil.copytree(ROOT / "src", repo / "src", ignore=shutil.ignore_patterns("__pycache__"))
    cfg = tmp_path_factory.mktemp("cfg") / "gitconfig"
    cfg.write_text("[user]\n\tname = t\n\temail = t@t.invalid\n")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(cfg), "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "code"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env, capture_output=True)
    return repo


@pytest.fixture(scope="module")
def uv_tools(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("uvtools")


@pytest.fixture
def home(tmp_path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def run(home, code_repo, uv_tools):
    def _run(*args: str, env: dict | None = None, path_prefix: str | None = None):
        bin_dir = uv_tools / "bin"
        full_env = {
            "HOME": str(home),
            "PATH": ":".join(filter(None, [path_prefix, str(bin_dir), os.environ["PATH"]])),
            "UV_TOOL_DIR": str(uv_tools / "tools"),
            "UV_TOOL_BIN_DIR": str(bin_dir),
            "UV_CACHE_DIR": REAL_UV_CACHE,
            "CORKERN_CODE_REPO": str(code_repo),
            "CORKERN_CODE_REF": "main",
            "GIT_CONFIG_GLOBAL": os.environ["GIT_CONFIG_GLOBAL"],
            "GIT_CONFIG_NOSYSTEM": "1",
            **(env or {}),
        }
        return subprocess.run(["bash", str(INSTALL), *args], env=full_env,
                              capture_output=True, text=True, timeout=300)
    return _run


def make_knowledge_repo(path: Path) -> Path:
    assert CliRunner().invoke(app, ["init", str(path)]).exit_code == 0
    return path


def assert_nothing_installed(home: Path):
    assert not (home / ".local" / "share" / "corkern").exists()
    assert not (home / ".corkern").exists()


def assert_no_v1_side_effects(home: Path, uv_tools: Path):
    assert not (home / ".claude").exists()
    assert not (home / ".bashrc").exists()
    cmd = uv_tools / "bin" / "corkern"                      # v2's own command, not a link into v1
    if cmd.is_symlink():
        assert not (cmd.resolve().parent / "projects.conf").exists()
    assert not (uv_tools / "bin" / "corkern2").exists()   # no leftover pre-rename command


class TestKnowledgeRepoModes:
    def test_existing_memory_dir(self, run, home, uv_tools):
        kr = make_knowledge_repo(home / "kr")
        r = run("--memory-dir", str(kr))
        assert r.returncode == 0, r.stderr + r.stdout
        hooks = home / ".local/share/corkern" / "hooks"
        assert (hooks / "corkern.sh").is_file() and (hooks / "VERSION").is_file()   # from the package
        assert not (home / ".local/share/corkern" / ".git").exists()                # no clone kept
        assert (uv_tools / "bin" / "corkern").exists()
        assert f"corkern on this machine uses {kr}" in r.stdout
        assert f"memory_dir={kr}" in (home / ".config/corkern/machine.conf").read_text()
        assert "CORKERN_MACHINE_REPO" not in (uv_tools / "bin" / "corkern").read_text()   # uv's own command
        assert_no_v1_side_effects(home, uv_tools)

    def test_existing_from_env(self, run, home):
        kr = make_knowledge_repo(home / "kr")
        r = run(env={"CORKERN_MEMORY_DIR": str(kr)})
        assert r.returncode == 0, r.stderr + r.stdout

    def test_existing_default_location(self, run, home):
        make_knowledge_repo(home / ".corkern")
        r = run()
        assert r.returncode == 0, r.stderr + r.stdout
        assert f"corkern on this machine uses {home / '.corkern'}" in r.stdout

    def test_new(self, run, home, uv_tools):
        r = run("--new")
        assert r.returncode == 0, r.stderr + r.stdout
        assert "export CORKERN_MEMORY_DIR=" not in r.stdout          # machine.conf knows it
        assert "Commit signing not set up" in r.stdout and "corkern signing" in r.stdout
        assert not list((home / ".ssh").glob("corkern_signing_*")) if (home / ".ssh").exists() else True
        assert (home / ".corkern" / "corkern.conf").is_file()
        assert (home / ".corkern" / ".git").is_dir()
        assert_no_v1_side_effects(home, uv_tools)

    def test_clone(self, run, home, tmp_path):
        src = make_knowledge_repo(tmp_path / "src-kr")
        bare = tmp_path / "kr.git"
        _git("clone", "-q", "--bare", str(src), str(bare))
        r = run("--clone", str(bare))
        assert r.returncode == 0, r.stderr + r.stdout
        assert (home / ".corkern" / "corkern.conf").is_file()

    def test_migrate_from_v1_export(self, run, home, tmp_path, uv_tools):
        v1 = tmp_path / "v1"
        (v1 / "memory" / "proj").mkdir(parents=True)
        (v1 / "memory" / "proj" / "MEMORY.md").write_text("- [a](a.md) — first\n")
        (v1 / "memory" / "proj" / "a.md").write_text("a fact\n")
        (v1 / "projects.conf").write_text("proj:dev/proj\n")
        bundle = tmp_path / "v1-export.tar"
        subprocess.run(["tar", "-cf", str(bundle), "-C", str(v1), "memory", "projects.conf"], check=True)
        r = run("--migrate", str(bundle))
        assert r.returncode == 0, r.stderr + r.stdout
        kr = home / ".corkern"
        assert (kr / "corkern.conf").read_text().strip().endswith("proj:dev/proj")
        assert (kr / "memory" / "proj" / "a.md").read_text() == "a fact\n"
        assert "gh repo create <name> --private" in r.stdout and "install.sh --clone" in r.stdout
        assert_no_v1_side_effects(home, uv_tools)

    def test_migrate_missing_export_installs_nothing(self, run, home, tmp_path):
        r = run("--migrate", str(tmp_path / "nope.tar"))
        assert r.returncode != 0 and "v1 export not found" in r.stderr
        assert_nothing_installed(home)

    def test_clone_rejects_non_knowledge_repo(self, run, home, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        (other / "x").write_text("x")
        _git("init", "-q", cwd=other)
        _git("add", "-A", cwd=other)
        _git("commit", "-q", "-m", "x", cwd=other)
        r = run("--clone", str(other))
        assert r.returncode != 0
        assert "not a corkern knowledge repo" in r.stderr

    def test_create_github_uses_private_repo(self, run, home, tmp_path):
        stub_dir = tmp_path / "stub"
        stub_dir.mkdir()
        log = tmp_path / "gh.log"
        (stub_dir / "gh").write_text(f'#!/bin/bash\necho "$@" >> {log}\n')
        (stub_dir / "gh").chmod(0o755)
        r = run("--create-github", "my-memory", path_prefix=str(stub_dir))
        assert r.returncode == 0, r.stderr + r.stdout
        call = log.read_text()
        assert "repo create my-memory --private" in call
        assert f"--source {home / '.corkern'}" in call

    def test_rerun_updates_in_place(self, run, home):
        kr = make_knowledge_repo(home / "kr")
        assert run("--memory-dir", str(kr)).returncode == 0
        r = run("--memory-dir", str(kr))
        assert r.returncode == 0, r.stderr + r.stdout


class TestRefusals:
    def test_no_knowledge_repo_choice(self, run, home):
        r = run()
        assert r.returncode != 0
        assert "--new" in r.stderr and "--clone" in r.stderr
        assert_nothing_installed(home)

    def test_existing_dir_without_conf(self, run, home):
        (home / "empty").mkdir()
        r = run("--memory-dir", str(home / "empty"))
        assert r.returncode != 0
        assert "not a corkern knowledge repo" in r.stderr
        assert_nothing_installed(home)

    @pytest.mark.parametrize("setup", ["sentinel", "projects_conf", "dev_corkern", "claude_dir"])
    def test_v1_memory_dir_refused_before_anything(self, run, home, setup):
        if setup == "sentinel":
            target = home / "v1"
            target.mkdir()
            (target / ".corkern-v1.sentinel").touch()
        elif setup == "projects_conf":
            target = home / "v1"
            target.mkdir()
            (target / "projects.conf").write_text("a:/a\n")
        elif setup == "dev_corkern":
            target = home / "dev" / "corkern"
            target.mkdir(parents=True)
        else:
            target = home / ".claude" / "projects" / "x" / "memory"
        r = run("--new", "--memory-dir", str(target / "kr") if setup != "claude_dir" else str(target))
        assert r.returncode != 0
        assert "v1-protected" in r.stderr
        assert_nothing_installed(home)
        assert not (target / "kr").exists()

    def test_v1_code_dir_refused(self, run, home):
        v1 = home / "dev" / "corkern"
        v1.mkdir(parents=True)
        r = run("--new", env={"CORKERN_CODE_DIR": str(v1 / "app")})
        assert r.returncode != 0
        assert "v1-protected" in r.stderr
        assert not (v1 / "app").exists()

    def test_conflicting_modes(self, run, home):
        r = run("--new", "--clone", "x")
        assert r.returncode != 0
        assert_nothing_installed(home)


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck():
    r = subprocess.run(["shellcheck", str(INSTALL)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_machine_conf_gives_corkern_this_machines_repo(run, home, uv_tools):
    """Shared dotfiles: nothing machine-specific in ~/.claude — machine.conf knows the repo."""
    kr = make_knowledge_repo(home / "kr")
    assert run("--memory-dir", str(kr)).returncode == 0
    cmd = uv_tools / "bin" / "corkern"
    env = {k: v for k, v in os.environ.items() if k not in ("CORKERN_MEMORY_DIR", "XDG_CONFIG_HOME")}
    out = subprocess.run([str(cmd), "list"], capture_output=True, text=True, env={**env, "HOME": str(home)})
    assert out.returncode == 0, out.stderr
    other = make_knowledge_repo(home / "other")             # the environment still wins
    out = subprocess.run([str(cmd), "status"], capture_output=True, text=True,
                         env={**env, "HOME": str(home), "CORKERN_MEMORY_DIR": str(other)})
    assert str(other) in out.stdout + out.stderr



def test_old_git_clone_install_is_moved_aside(run, home):
    """Before 2026-10-13 install.sh kept a git checkout in ~/.local/share/corkern."""
    kr = make_knowledge_repo(home / "kr")
    old = home / ".local/share/corkern"
    (old / ".git").mkdir(parents=True)
    (old / "marker").write_text("old clone")
    r = run("--memory-dir", str(kr))
    assert r.returncode == 0, r.stderr + r.stdout
    kept = list((home / ".local/share").glob("corkern.old-clone-*"))
    assert len(kept) == 1 and (kept[0] / "marker").read_text() == "old clone"
    assert (old / "hooks" / "corkern.sh").is_file() and not (old / ".git").exists()



def test_install_refused_while_v1s_corkern_is_there(run, home, uv_tools, tmp_path):
    """v1 installs ~/.local/bin/corkern as a link into its repo; v2 must not replace it."""
    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / "projects.conf").write_text("x:y\n")
    (v1 / "corkern").write_text("#!/bin/bash\necho v1\n")
    (v1 / "corkern").chmod(0o755)
    (uv_tools / "bin").mkdir(parents=True, exist_ok=True)
    link = uv_tools / "bin" / "corkern"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(v1 / "corkern")
    try:
        r = run("--new")
        assert r.returncode != 0 and "corkern v1 is installed here" in r.stderr
        assert link.is_symlink() and link.resolve() == (v1 / "corkern").resolve()   # untouched
        assert not (home / ".corkern").exists()
    finally:
        link.unlink()
