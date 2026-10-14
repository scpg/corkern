import pytest
from pathlib import Path
from corkern.config import MemoryRepo, load_config


class TestMemoryRepoGuard:
    def test_raises_on_v1_sentinel(self, v1_repo):
        with pytest.raises(SystemExit):
            MemoryRepo(v1_repo)

    def test_ok_without_sentinel(self, test_repo):
        repo = MemoryRepo(test_repo)
        assert repo.path == test_repo.resolve()
        assert repo.memory_dir == test_repo.resolve() / "memory"

    def test_write_rechecks_guard(self, test_repo):
        repo = MemoryRepo(test_repo)
        (test_repo / ".corkern-v1.sentinel").touch()
        with pytest.raises(SystemExit):
            repo.write("slug", "file.md", "content")
        assert not (test_repo / "memory" / "slug").exists()

    def test_memory_dir_symlinked_into_v1_refused(self, tmp_path):
        v1 = tmp_path / "v1"
        (v1 / "memory").mkdir(parents=True)
        (v1 / ".corkern-v1.sentinel").touch()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "corkern.conf").write_text("")
        (repo_dir / "memory").symlink_to(v1 / "memory")
        with pytest.raises(SystemExit):
            MemoryRepo(repo_dir)


class TestMemoryRepoIO:
    def test_slugs_from_memory_subdir(self, test_repo):
        repo = MemoryRepo(test_repo)
        assert repo.slugs() == ["alpha", "beta"]

    def test_slugs_skip_hidden_and_files(self, test_repo):
        (test_repo / "memory" / ".archive").mkdir()
        (test_repo / "memory" / "README.md").write_text("")
        assert MemoryRepo(test_repo).slugs() == ["alpha", "beta"]

    def test_slugs_no_memory_dir(self, tmp_path):
        assert MemoryRepo(tmp_path).slugs() == []

    def test_write_creates_parents_under_memory(self, test_repo):
        MemoryRepo(test_repo).write("newslug", "notes.md", "hello")
        assert (test_repo / "memory" / "newslug" / "notes.md").read_text() == "hello"

    def test_read_returns_content(self, test_repo):
        (test_repo / "memory" / "alpha" / "f.md").write_text("data")
        assert MemoryRepo(test_repo).read("alpha", "f.md") == "data"

    @pytest.mark.parametrize("slug,filename", [
        ("../../evil", "file.md"),
        ("..", "corkern.conf"),
        ("alpha", "../../corkern.conf"),
        ("/etc", "passwd"),
    ])
    def test_write_blocks_path_traversal(self, test_repo, slug, filename):
        with pytest.raises(ValueError, match="Path traversal"):
            MemoryRepo(test_repo).write(slug, filename, "bad")

    def test_prefix_sibling_is_traversal(self, tmp_path):
        # /x/repo/memory-evil must not count as inside /x/repo/memory
        repo_dir = tmp_path / "repo"
        (repo_dir / "memory").mkdir(parents=True)
        with pytest.raises(ValueError, match="Path traversal"):
            MemoryRepo(repo_dir).write("../memory-evil", "f.md", "bad")


class TestLoadConfig:
    def test_raises_without_env(self, test_repo, monkeypatch):
        monkeypatch.delenv("CORKERN_MEMORY_DIR", raising=False)
        with pytest.raises(SystemExit):
            load_config()

    def test_raises_on_missing_dir(self, monkeypatch):
        monkeypatch.setenv("CORKERN_MEMORY_DIR", "/nonexistent/path/xyz")
        with pytest.raises(SystemExit):
            load_config()

    def test_raises_without_corkern_conf(self, tmp_path, monkeypatch):
        # Positive identification: only a v2 knowledge repo (has corkern.conf) is accepted
        monkeypatch.setenv("CORKERN_MEMORY_DIR", str(tmp_path))
        with pytest.raises(SystemExit, match="not a corkern v2 knowledge repo"):
            load_config()

    def test_raises_on_v1_layout(self, tmp_path, monkeypatch):
        (tmp_path / "corkern.conf").write_text("")
        (tmp_path / "projects.conf").write_text("corkern:/x\n")
        monkeypatch.setenv("CORKERN_MEMORY_DIR", str(tmp_path))
        with pytest.raises(SystemExit, match="v1-protected"):
            load_config()

    def test_loads_projects(self, test_repo, monkeypatch):
        monkeypatch.setenv("CORKERN_MEMORY_DIR", str(test_repo))
        _, projects = load_config()
        assert projects["alpha"] == Path("/tmp/alpha")

    def test_local_conf_overrides(self, test_repo, monkeypatch):
        (test_repo / "corkern.conf.local").write_text("alpha:/tmp/alpha-local\n")
        monkeypatch.setenv("CORKERN_MEMORY_DIR", str(test_repo))
        _, projects = load_config()
        assert projects["alpha"] == Path("/tmp/alpha-local")

    def test_v1_semantics_relative_to_home(self, tmp_path, monkeypatch):
        """corkern.conf follows v1's projects.conf rules (see v1 resolve_path)."""
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        (tmp_path / "corkern.conf").write_text(
            "# slug:path\n"
            "rel:dev/projects/x\n"
            "abs:/opt/y\n"
            "tilde:~/z\n"
            "home:\n"
            "win:C:/Users/me/proj\n"
            "dup:dev/first\n"
            "dup:dev/second\n"
            "crlf:dev/w\r\n"
        )
        monkeypatch.setenv("CORKERN_MEMORY_DIR", str(tmp_path))
        _, p = load_config()
        assert p["rel"] == home / "dev/projects/x"
        assert p["abs"] == Path("/opt/y")
        assert p["tilde"] == home / "z"
        assert p["home"] == home
        assert "win" not in p            # Windows-only line, skipped on Linux
        assert p["dup"] == home / "dev/first"  # first match wins
        assert p["crlf"] == home / "dev/w"

    def test_local_overrides_even_with_duplicates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "corkern.conf").write_text("a:dev/main\n")
        (tmp_path / "corkern.conf.local").write_text("a:/local/one\na:/local/two\n")
        monkeypatch.setenv("CORKERN_MEMORY_DIR", str(tmp_path))
        _, p = load_config()
        assert p["a"] == Path("/local/one")

    def test_ignores_comments_and_blank_lines(self, tmp_path, monkeypatch):
        (tmp_path / "corkern.conf").write_text("# comment\n\nalpha:/tmp/a\n")
        monkeypatch.setenv("CORKERN_MEMORY_DIR", str(tmp_path))
        _, projects = load_config()
        assert list(projects.keys()) == ["alpha"]
