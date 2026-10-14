from tests.conftest import list_row, listed_here  # noqa: F401
import pytest
from typer.testing import CliRunner
from corkern.main import app

runner = CliRunner()


def invoke(args, env=None):
    return runner.invoke(app, args, env=env or {})


class TestAdd:
    def test_adds_new_project(self, test_repo):
        result = invoke(
            ["add", "gamma", "/tmp/gamma"],
            env={"CORKERN_MEMORY_DIR": str(test_repo)},
        )
        assert result.exit_code == 0
        conf = (test_repo / "corkern.conf").read_text()
        assert "gamma:/tmp/gamma" in conf

    def test_refuses_duplicate_slug(self, test_repo):
        result = invoke(
            ["add", "alpha", "/tmp/other"],
            env={"CORKERN_MEMORY_DIR": str(test_repo)},
        )
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_defaults_to_cwd(self, test_repo, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = invoke(["add", "newproj"], env={"CORKERN_MEMORY_DIR": str(test_repo)})
        assert result.exit_code == 0
        conf = (test_repo / "corkern.conf").read_text()
        assert f"newproj:{tmp_path}" in conf


class TestAddV1Format:
    def test_stores_home_relative_path(self, test_repo, tmp_path):
        home = tmp_path / "home"
        (home / "dev" / "p").mkdir(parents=True)
        env = {"CORKERN_MEMORY_DIR": str(test_repo), "HOME": str(home)}
        assert invoke(["add", "p", str(home / "dev" / "p")], env=env).exit_code == 0
        assert "p:dev/p\n" in (test_repo / "corkern.conf").read_text()
        assert listed_here(invoke(["list"], env=env).output, "p")

    def test_home_itself_stored_empty(self, test_repo, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        env = {"CORKERN_MEMORY_DIR": str(test_repo), "HOME": str(home)}
        assert invoke(["add", "h", str(home)], env=env).exit_code == 0
        assert "h:\n" in (test_repo / "corkern.conf").read_text()

    def test_outside_home_stays_absolute(self, test_repo, tmp_path):
        env = {"CORKERN_MEMORY_DIR": str(test_repo), "HOME": str(tmp_path / "home")}
        invoke(["add", "o", "/opt/o"], env=env)
        assert "o:/opt/o\n" in (test_repo / "corkern.conf").read_text()


class TestSlugValidation:
    @pytest.mark.parametrize("slug", ["a:b", "a/b", "../x", ".hidden", "-x", "a b", "a\nb", ""])
    def test_rejects_bad_slug(self, test_repo, slug):
        before = (test_repo / "corkern.conf").read_text()
        result = invoke(["add", "--", slug, "/tmp"], env={"CORKERN_MEMORY_DIR": str(test_repo)})
        assert result.exit_code != 0
        assert "invalid slug" in result.output.lower()
        assert (test_repo / "corkern.conf").read_text() == before

    @pytest.mark.parametrize("slug", ["webshop", "toolbox", "sd-theProductName", "corkern-v2", "a.b_c"])
    def test_accepts_v1_style_slugs(self, test_repo, slug):
        result = invoke(["add", slug, "/tmp"], env={"CORKERN_MEMORY_DIR": str(test_repo)})
        assert result.exit_code == 0, result.output

    def test_rejects_newline_in_path(self, test_repo):
        result = invoke(["add", "ok", "/tmp/a\nevil:/x"], env={"CORKERN_MEMORY_DIR": str(test_repo)})
        assert result.exit_code != 0
        assert "evil" not in (test_repo / "corkern.conf").read_text()


class TestRemove:
    def test_removes_existing_slug(self, test_repo):
        result = invoke(["remove", "alpha"], env={"CORKERN_MEMORY_DIR": str(test_repo)})
        assert result.exit_code == 0
        conf = (test_repo / "corkern.conf").read_text()
        assert "alpha" not in conf
        assert "beta" in conf

    def test_refuses_unknown_slug(self, test_repo):
        result = invoke(["remove", "unknown"], env={"CORKERN_MEMORY_DIR": str(test_repo)})
        assert result.exit_code != 0
        assert "not found" in result.output


class TestStatus:
    def test_shows_memory_dir(self, test_repo):
        result = invoke(["status"], env={"CORKERN_MEMORY_DIR": str(test_repo)})
        assert result.exit_code == 0
        assert str(test_repo) in result.output

    def test_shows_project_count(self, test_repo):
        result = invoke(["status"], env={"CORKERN_MEMORY_DIR": str(test_repo)})
        assert "2" in result.output

    def test_fails_without_env(self, monkeypatch):
        monkeypatch.delenv("CORKERN_MEMORY_DIR", raising=False)
        result = invoke(["status"])
        assert result.exit_code != 0


class TestList:
    def test_lists_projects(self, test_repo):
        result = invoke(["list"], env={"CORKERN_MEMORY_DIR": str(test_repo)})
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_empty_repo(self, tmp_path):
        (tmp_path / "corkern.conf").write_text("")
        result = invoke(["list"], env={"CORKERN_MEMORY_DIR": str(tmp_path)})
        assert result.exit_code == 0
        assert "No projects tracked" in result.output

    def test_missing_env(self, monkeypatch):
        monkeypatch.delenv("CORKERN_MEMORY_DIR", raising=False)
        result = invoke(["list"])
        assert result.exit_code != 0
