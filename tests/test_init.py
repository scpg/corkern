import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corkern.knowledge_repo import TEMPLATE
from corkern.main import app

runner = CliRunner()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout


def init(path: Path | None = None, env: dict | None = None):
    args = ["init"] + ([str(path)] if path else [])
    return runner.invoke(app, args, env=env or {})


class TestInit:
    def test_creates_template(self, tmp_path):
        repo = tmp_path / "kr"
        result = init(repo)
        assert result.exit_code == 0, result.output
        for rel in TEMPLATE:
            assert (repo / rel).is_file(), rel
        assert (repo / "memory" / "global").is_dir()
        assert (repo / ".devices").is_dir()

    def test_git_repo_with_initial_commit(self, tmp_path):
        repo = tmp_path / "kr"
        init(repo)
        assert git(repo, "rev-list", "--count", "HEAD").strip() == "1"
        assert git(repo, "status", "--porcelain") == ""
        assert git(repo, "branch", "--show-current").strip() == "main"

    def test_local_conf_is_gitignored(self, tmp_path):
        repo = tmp_path / "kr"
        init(repo)
        (repo / "corkern.conf.local").write_text("x:/y\n")
        assert git(repo, "status", "--porcelain") == ""

    def test_usable_by_other_commands(self, tmp_path):
        repo = tmp_path / "kr"
        init(repo)
        env = {"CORKERN_MEMORY_DIR": str(repo)}
        assert runner.invoke(app, ["add", "proj", "/tmp"], env=env).exit_code == 0
        result = runner.invoke(app, ["list"], env=env)
        assert "proj" in result.output

    def test_into_existing_empty_dir(self, tmp_path):
        assert init(tmp_path).exit_code == 0
        assert (tmp_path / "corkern.conf").is_file()

    def test_defaults_to_env(self, tmp_path):
        repo = tmp_path / "kr"
        result = init(env={"CORKERN_MEMORY_DIR": str(repo)})
        assert result.exit_code == 0
        assert (repo / "corkern.conf").is_file()

    def test_requires_path_or_env(self, monkeypatch):
        monkeypatch.delenv("CORKERN_MEMORY_DIR", raising=False)
        assert init().exit_code != 0


class TestInitResume:
    def test_rerun_finishes_after_commit_failure(self, tmp_path, monkeypatch):
        repo = tmp_path / "kr"
        no_identity = tmp_path / "noid.gitconfig"
        no_identity.write_text("[user]\n\tuseConfigOnly = true\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(no_identity))
        for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
            monkeypatch.delenv(var, raising=False)
        first = init(repo)
        assert first.exit_code != 0
        assert "git commit" in first.output

        ok = tmp_path / "ok.gitconfig"
        ok.write_text("[user]\n\tname = t\n\temail = t@t.invalid\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(ok))
        second = init(repo)
        assert second.exit_code == 0, second.output
        assert git(repo, "rev-list", "--count", "HEAD").strip() == "1"
        assert git(repo, "status", "--porcelain") == ""

    def test_resume_keeps_user_edits(self, tmp_path):
        repo = tmp_path / "kr"
        repo.mkdir()
        (repo / "corkern.conf").write_text("mine:/x\n")  # pre-existing, no git yet
        result = init(repo)
        assert result.exit_code == 0, result.output
        assert (repo / "corkern.conf").read_text() == "mine:/x\n"
        assert (repo / "global" / "CLAUDE.md").is_file()


class TestInitRefusals:
    def test_idempotent_on_existing_knowledge_repo(self, tmp_path):
        repo = tmp_path / "kr"
        init(repo)
        result = init(repo)
        assert result.exit_code == 0
        assert "already" in result.output
        assert git(repo, "rev-list", "--count", "HEAD").strip() == "1"

    def test_refuses_non_empty_dir(self, tmp_path):
        (tmp_path / "unrelated.txt").write_text("keep me")
        result = init(tmp_path)
        assert result.exit_code != 0
        assert "not empty" in result.output
        assert sorted(p.name for p in tmp_path.iterdir()) == ["unrelated.txt"]

    def test_refuses_file_with_clear_message(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        result = init(f)
        assert result.exit_code != 0
        assert "not a directory" in result.output
        assert f.read_text() == "x"

    def test_refuses_v1_and_creates_nothing(self, v1_repo):
        target = v1_repo / "new-kr"
        result = init(target)
        assert result.exit_code != 0
        assert "v1-protected" in result.output
        assert not target.exists()

    def test_refuses_v1_layout_dir(self, tmp_path):
        (tmp_path / "projects.conf").write_text("corkern:/x\n")
        result = init(tmp_path)
        assert result.exit_code != 0
        assert not (tmp_path / "corkern.conf").exists()
