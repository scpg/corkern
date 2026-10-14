"""corkern migrate — v1 export bundle (tarball) → new v2 knowledge repo.

Bundles here are synthetic; v2 never reads ~/dev/corkern directly.
"""
import io
import subprocess
import tarfile
from pathlib import Path

import pytest
from tests.conftest import list_row, listed_here  # noqa: F401
from typer.testing import CliRunner

from corkern.main import app

runner = CliRunner()

V1_FILES = {
    "projects.conf": "alpha:/tmp/alpha\nbeta:/tmp/beta\n",
    "memory/alpha/MEMORY.md": "# alpha\n",
    "memory/alpha/note.md": "alpha note\n",
    "memory/alpha/sessions/2026-10-01-1200.md": "- session\n",
    "memory/global/todo.md": "- [ ] x\n",
    ".devices/host-a": "host:host-a\n",
    "global/CLAUDE.md": "# my global instructions\n",
    "hooks/corkern-push.sh": "#!/bin/bash\n",  # v1 code — must not be migrated
}


def make_bundle(tmp_path: Path, files: dict[str, str] = V1_FILES, name="v1.tar") -> Path:
    bundle = tmp_path / name
    with tarfile.open(bundle, "w") as tar:
        for rel, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return bundle


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout


def migrate(*args: str, env=None):
    return runner.invoke(app, ["migrate", *map(str, args)], env=env or {})


class TestMigrate:
    def test_copies_v1_data(self, tmp_path):
        dest = tmp_path / "kr"
        result = migrate(make_bundle(tmp_path), dest)
        assert result.exit_code == 0, result.output
        assert (dest / "corkern.conf").read_text() == V1_FILES["projects.conf"]
        for rel in ["memory/alpha/MEMORY.md", "memory/alpha/note.md",
                    "memory/alpha/sessions/2026-10-01-1200.md", "memory/global/todo.md",
                    ".devices/host-a", "global/CLAUDE.md"]:
            assert (dest / rel).read_text() == V1_FILES[rel], rel

    def test_v1_code_and_registry_not_copied(self, tmp_path):
        dest = tmp_path / "kr"
        migrate(make_bundle(tmp_path), dest)
        assert not (dest / "hooks").exists()
        assert not (dest / "projects.conf").exists()

    def test_adds_template_files(self, tmp_path):
        dest = tmp_path / "kr"
        migrate(make_bundle(tmp_path), dest)
        assert "corkern.conf.local" in (dest / ".gitignore").read_text()
        assert (dest / "README.md").is_file()

    def test_single_commit_clean_tree(self, tmp_path):
        dest = tmp_path / "kr"
        migrate(make_bundle(tmp_path), dest)
        assert git(dest, "rev-list", "--count", "HEAD").strip() == "1"
        assert git(dest, "status", "--porcelain") == ""
        assert "migrate" in git(dest, "log", "-1", "--format=%s")

    def test_result_usable(self, tmp_path):
        dest = tmp_path / "kr"
        migrate(make_bundle(tmp_path), dest)
        env = {"CORKERN_MEMORY_DIR": str(dest)}
        out = runner.invoke(app, ["list"], env=env).output
        assert "alpha" in out and "beta" in out

    def test_summary_reports_counts_and_skips(self, tmp_path):
        result = migrate(make_bundle(tmp_path), tmp_path / "kr")
        assert "memory files : 4" in result.output
        assert "skipped      : 1" in result.output
        assert "hooks/corkern-push.sh" in result.output

    def test_dest_defaults_to_env(self, tmp_path):
        dest = tmp_path / "kr"
        result = migrate(make_bundle(tmp_path), env={"CORKERN_MEMORY_DIR": str(dest)})
        assert result.exit_code == 0, result.output
        assert (dest / "corkern.conf").is_file()

    def test_dry_run_writes_nothing(self, tmp_path):
        dest = tmp_path / "kr"
        result = migrate(make_bundle(tmp_path), dest, "--dry-run")
        assert result.exit_code == 0, result.output
        assert "memory files : 4" in result.output
        assert not dest.exists()


def test_documented_git_archive_command(tmp_path_factory):
    """The export command from `migrate --help`, run against a synthetic v1-style repo."""
    fake_v1 = tmp_path_factory.mktemp("fakev1")
    for rel, content in V1_FILES.items():
        (fake_v1 / rel).parent.mkdir(parents=True, exist_ok=True)
        (fake_v1 / rel).write_text(content)
    subprocess.run(["git", "-C", str(fake_v1), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(fake_v1), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(fake_v1), "commit", "-q", "-m", "v1"], check=True)
    out = tmp_path_factory.mktemp("export") / "v1-export.tar"
    with out.open("wb") as f:
        subprocess.run(["git", "-C", str(fake_v1), "archive", "HEAD",
                        "memory", ".devices", "global", "projects.conf"], stdout=f, check=True)

    dest = tmp_path_factory.mktemp("dest") / "kr"
    result = migrate(out, dest)
    assert result.exit_code == 0, result.output
    assert "memory files : 4" in result.output
    assert "skipped      : 0" in result.output  # hooks/ not in the archive pathspec
    assert (dest / "memory/alpha/sessions/2026-10-01-1200.md").is_file()


def test_v1_relative_paths_resolve_after_migration(tmp_path):
    """Regression (/verify): v1 stores paths relative to HOME; they must resolve, not show ✗."""
    home = tmp_path / "home"
    (home / "dev" / "projects" / "webshop").mkdir(parents=True)
    files = dict(V1_FILES)
    files["projects.conf"] = "# slug:path\nwebshop:dev/projects/webshop\nhome:\n"
    dest = tmp_path / "kr"
    assert migrate(make_bundle(tmp_path, files), dest).exit_code == 0
    out = runner.invoke(app, ["list"], env={"CORKERN_MEMORY_DIR": str(dest), "HOME": str(home)}).output
    assert listed_here(out, "webshop")
    assert listed_here(out, "home")


def test_reminds_bundle_has_committed_data_only(tmp_path):
    result = migrate(make_bundle(tmp_path), tmp_path / "kr", "--dry-run")
    assert "committed" in result.output


def test_refuses_dest_that_is_a_file(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    result = migrate(make_bundle(tmp_path), f)
    assert result.exit_code != 0
    assert "not a directory" in result.output


class TestMigrateRefusals:
    def test_refuses_non_empty_dest(self, tmp_path):
        dest = tmp_path / "kr"
        dest.mkdir()
        (dest / "keep.txt").write_text("x")
        result = migrate(make_bundle(tmp_path), dest)
        assert result.exit_code != 0
        assert "not empty" in result.output
        assert sorted(p.name for p in dest.iterdir()) == ["keep.txt"]

    # v1_repo *is* tmp_path, so the other side uses a separate dir to isolate the cause
    def test_refuses_dest_in_v1(self, tmp_path_factory, v1_repo):
        bundle = make_bundle(tmp_path_factory.mktemp("bundle"))
        result = migrate(bundle, v1_repo / "new")
        assert result.exit_code != 0
        assert "v1-protected" in result.output
        assert not (v1_repo / "new").exists()

    def test_refuses_bundle_inside_v1(self, tmp_path_factory, v1_repo):
        bundle = make_bundle(v1_repo)
        dest = tmp_path_factory.mktemp("dest") / "kr"
        result = migrate(bundle, dest)
        assert result.exit_code != 0
        assert "v1-protected" in result.output
        assert not dest.exists()

    def test_refuses_bundle_without_memory(self, tmp_path):
        result = migrate(make_bundle(tmp_path, {"projects.conf": "a:/a\n"}), tmp_path / "kr")
        assert result.exit_code != 0
        assert "no memory/" in result.output
        assert not (tmp_path / "kr").exists()

    def test_refuses_missing_bundle(self, tmp_path):
        result = migrate(tmp_path / "nope.tar", tmp_path / "kr")
        assert result.exit_code != 0

    def test_refuses_non_tar(self, tmp_path):
        bad = tmp_path / "bad.tar"
        bad.write_text("not a tarball")
        result = migrate(bad, tmp_path / "kr")
        assert result.exit_code != 0
        assert "not a valid tar" in result.output


class TestBundleSafety:
    @pytest.mark.parametrize("evil", ["../escape.md", "/abs/escape.md", "memory/../../escape.md"])
    def test_rejects_traversal_entries(self, tmp_path, evil):
        files = dict(V1_FILES)
        files[evil] = "pwned"
        result = migrate(make_bundle(tmp_path, files), tmp_path / "kr")
        assert result.exit_code != 0
        assert "unsafe entry" in result.output
        assert not (tmp_path / "escape.md").exists()
        assert not (tmp_path / "kr").exists()

    def test_skips_symlink_entries(self, tmp_path):
        bundle = make_bundle(tmp_path)
        with tarfile.open(bundle, "a") as tar:
            link = tarfile.TarInfo("memory/alpha/link.md")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            tar.addfile(link)
        dest = tmp_path / "kr"
        result = migrate(bundle, dest)
        assert result.exit_code == 0, result.output
        assert not (dest / "memory/alpha/link.md").exists()
        assert "memory/alpha/link.md" in result.output  # reported as skipped
