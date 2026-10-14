"""v1 isolation guard — unit tests (synthetic paths only, never the real v1 repo)."""
import pytest
from pathlib import Path
from corkern import guard
from corkern.guard import V1ProtectedError


@pytest.fixture
def fake_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


class TestRefusedPaths:
    def test_sentinel_at_path(self, v1_repo):
        with pytest.raises(V1ProtectedError):
            guard.assert_safe(v1_repo)

    def test_sentinel_in_ancestor(self, v1_repo):
        with pytest.raises(V1ProtectedError):
            guard.assert_safe(v1_repo / "memory" / "slug" / "file.md")

    def test_v1_registry_signature_in_ancestor(self, tmp_path):
        (tmp_path / "projects.conf").write_text("corkern:/x\n")
        with pytest.raises(V1ProtectedError):
            guard.assert_safe(tmp_path / "memory" / "corkern")

    def test_known_v1_location_without_sentinel(self, fake_home):
        v1 = fake_home / "dev" / "corkern"
        (v1 / "memory").mkdir(parents=True)
        with pytest.raises(V1ProtectedError):
            guard.assert_safe(v1 / "memory")

    def test_corkern_env_var_location(self, tmp_path, monkeypatch):
        v1 = tmp_path / "elsewhere"
        v1.mkdir()
        monkeypatch.setenv("CORKERN", str(v1))
        with pytest.raises(V1ProtectedError):
            guard.assert_safe(v1 / "memory")

    def test_symlink_into_v1(self, v1_repo, tmp_path):
        (v1_repo / "memory").mkdir()
        link = tmp_path / "innocent"
        link.symlink_to(v1_repo / "memory")
        with pytest.raises(V1ProtectedError):
            guard.assert_safe(link / "file.md")

    @pytest.mark.parametrize("rel", [
        ".claude/settings.json",
        ".claude/CLAUDE.md",
        ".claude/projects/-home-x/memory/file.md",
    ])
    def test_v1_runtime_files(self, fake_home, rel):
        with pytest.raises(V1ProtectedError):
            guard.assert_safe(fake_home / rel)

    def test_sibling_dir_is_not_v1(self, fake_home):
        # ~/dev/corkern-v2 must not be confused with ~/dev/corkern
        assert guard.assert_safe(fake_home / "dev" / "corkern-v2")

    def test_corkern_binary_allowed(self, fake_home):
        assert guard.assert_safe(fake_home / ".local" / "bin" / "corkern")

    def test_v1s_corkern_link_is_protected(self, fake_home):
        """v1 installs ~/.local/bin/corkern as a link into its repo (which holds projects.conf)."""
        repo = fake_home / "dev" / "corkern"
        repo.mkdir(parents=True)
        (repo / "projects.conf").write_text("x:y\n")
        (repo / "corkern").write_text("#!/bin/bash\n")
        bin_ = fake_home / ".local" / "bin"
        bin_.mkdir(parents=True)
        (bin_ / "corkern").symlink_to(repo / "corkern")
        assert guard.is_v1_command(bin_ / "corkern") and guard.v1_installed_for(fake_home)
        with pytest.raises(V1ProtectedError):
            guard.assert_safe(bin_ / "corkern")

    def test_v2s_corkern_command_is_not_v1(self, fake_home, tmp_path):
        """v2's corkern is uv's link into its tool environment — not v1, not protected."""
        env = tmp_path / "uv-tools" / "corkern" / "bin"
        env.mkdir(parents=True)
        (env / "corkern").write_text("#!/usr/bin/env python3\n")
        bin_ = fake_home / ".local" / "bin"
        bin_.mkdir(parents=True)
        (bin_ / "corkern").symlink_to(env / "corkern")
        assert not guard.is_v1_command(bin_ / "corkern") and not guard.v1_installed_for(fake_home)
        assert guard.assert_safe(bin_ / "corkern")


class TestGuardedIO:
    def test_write_and_read(self, tmp_path):
        target = tmp_path / "a" / "b.md"
        guard.write_text(target, "hi")
        assert guard.read_text(target) == "hi"

    def test_append(self, tmp_path):
        target = tmp_path / "f"
        guard.write_text(target, "a\n")
        guard.append_text(target, "b\n")
        assert guard.read_text(target) == "a\nb\n"

    def test_write_refused_leaves_no_file(self, v1_repo):
        target = v1_repo / "memory" / "x.md"
        with pytest.raises(V1ProtectedError):
            guard.write_text(target, "bad")
        assert not target.exists()
        assert not (v1_repo / "memory").exists()

    def test_read_refused(self, v1_repo):
        (v1_repo / "secret.md").write_text("v1 data")
        with pytest.raises(V1ProtectedError):
            guard.read_text(v1_repo / "secret.md")

    def test_list_dirs_refused(self, v1_repo):
        with pytest.raises(V1ProtectedError):
            guard.list_dirs(v1_repo)

    def test_list_dirs_skips_hidden_and_files(self, tmp_path):
        (tmp_path / "alpha").mkdir()
        (tmp_path / ".git").mkdir()
        (tmp_path / "file.md").write_text("")
        assert guard.list_dirs(tmp_path) == ["alpha"]

    def test_is_empty(self, tmp_path):
        assert guard.is_empty(tmp_path / "missing")
        assert guard.is_empty(tmp_path)
        (tmp_path / ".hidden").write_text("")
        assert not guard.is_empty(tmp_path)

    def test_run_git(self, tmp_path):
        guard.run_git(tmp_path, "init", "-q")
        assert "On branch" in guard.run_git(tmp_path, "status").stdout

    def test_run_git_refused_in_v1(self, v1_repo):
        with pytest.raises(V1ProtectedError):
            guard.run_git(v1_repo, "status")

    def test_run_git_failure_is_clear(self, tmp_path):
        with pytest.raises(guard.GitError, match="git log failed"):
            guard.run_git(tmp_path, "log")  # not a repo

    def test_write_bytes(self, tmp_path):
        guard.write_bytes(tmp_path / "d" / "f.bin", b"\x00\x01")
        assert (tmp_path / "d" / "f.bin").read_bytes() == b"\x00\x01"

    def test_read_tar(self, tmp_path):
        import io, tarfile
        bundle = tmp_path / "b.tar.gz"
        with tarfile.open(bundle, "w:gz") as tar:
            for name, data in [("./memory/a.md", b"A"), ("memory/sub/b.md", b"B")]:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            d = tarfile.TarInfo("memory/emptydir")
            d.type = tarfile.DIRTYPE
            tar.addfile(d)
        files, skipped = guard.read_tar(bundle)
        assert files == {"memory/a.md": b"A", "memory/sub/b.md": b"B"}
        assert skipped == []

    def test_read_tar_refused_in_v1(self, v1_repo):
        with pytest.raises(V1ProtectedError):
            guard.read_tar(v1_repo / "x.tar")

    def test_error_is_hard_stop(self, v1_repo):
        # SystemExit is not caught by `except Exception` — callers can't swallow it by accident
        with pytest.raises(SystemExit) as exc:
            guard.assert_safe(v1_repo)
        assert not isinstance(exc.value, Exception)
        assert "v1-protected" in str(exc.value.code)
