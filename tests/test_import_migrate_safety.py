"""Import and migrate never bring in a git directory or unscanned text (audit M5, M6).

M5  a crafted export carrying memory/<slug>/.git/config (core.fsmonitor=<cmd>) — or one of
    its NTFS/HFS look-alikes — breaks the commit hook (`git add -A -- memory/` fails) and runs
    the command when git is used in that folder: such members are refused, listed, never written
M6  an export holds working-tree files (not yet committed memories, files the hooks held back);
    migrate commits them only after the hooks' secret scan + redaction, and fails closed
"""
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from corkern import guard
from corkern.secret_scan import MARKER
from tests.test_export_import import craft, export_to, git, kr, run  # noqa: F401  (fixture)
from tests.test_hooks import hook

AWS = "AKIA" + "ABCDEFGHIJ234567"           # the fixtures' fake AWS key, built at runtime
EVIL = {
    "memory/x/.git/config": b"[core]\n\tfsmonitor = touch /tmp/corkern-pwned\n",
    "memory/x/.GIT/config": b"[core]\n\tfsmonitor = touch /tmp/corkern-pwned\n",
    "memory/x/.git./config": b"[core]\n\tfsmonitor = touch /tmp/corkern-pwned\n",
    "memory/x/git~1/config": b"[core]\n\tfsmonitor = touch /tmp/corkern-pwned\n",
}


# ── the shared check ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", [
    "memory/x/.git/config", "memory/x/.GIT/config", "memory/x/.Git/HEAD", ".git/config",
    "memory/x/.git./config", "memory/x/.git ./config", "memory/x/.git . ./hooks/x", "memory/x/.git::$INDEX_ALLOCATION/c",
    "memory/x/git~1/config", "memory/x/GIT~1/config",
    "memory/x/.g‌it/config", "memory/x/.gi‍t/config", "memory/x/﻿.git/config",
    "memory/x/.git\\config", "memory/../x.md", "../x.md", "/etc/passwd", "memory//x.md", "memory/./x.md", "",
])
def test_unsafe_member_refuses_git_components_and_traversal(path):
    assert guard.unsafe_member(path), path


@pytest.mark.parametrize("path", ["memory/x/a.md", "memory/x/.gitignore", "memory/x/.github/a.md",
                                  "memory/x/git/notes.md", "memory/x/.gitkeep", "memory/x/my.git.md",
                                  "memory/x/ünïcødé.md", "memory/x/with space.md"])
def test_unsafe_member_allows_ordinary_names(path):
    assert guard.unsafe_member(path) is None


# ── M5: import ───────────────────────────────────────────────────────────────────────────
def commit_hook_still_commits(repo, tmp):
    (repo / "memory" / "web" / "after-import.md").write_text("written after the import\n")
    hook("corkern-commit-memory", repo, tmp)
    return "memory/web/after-import.md" in git(repo, "log", "-1", "--name-only", "--format=")


def test_m5_import_never_writes_a_git_component(kr, tmp_path):
    src, crafted = tmp_path / "x.tar.gz", tmp_path / "evil.tar.gz"
    export_to(src)
    craft(src, crafted, {f"data/{rel}": data for rel, data in EVIL.items()})
    r = run("import", str(crafted), "--apply", "--yes")
    assert r.exit_code == 0, r.output
    for rel in EVIL:
        assert not (kr / rel).exists(), f"{rel} was written"
        assert f"skipped: {rel} — " in r.output, r.output
    assert not (kr / "memory" / "x").exists()
    assert commit_hook_still_commits(kr, tmp_path)


# ── M5: migrate (corkern export and git-archive bundle) ──────────────────────────────────
def tar_bundle(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w") as tar:
        for rel, data in files.items():
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_m5_migrate_from_export_never_writes_a_git_component(kr, tmp_path):
    src, crafted, dest = tmp_path / "x.tar.gz", tmp_path / "evil.tar.gz", tmp_path / "new"
    export_to(src)
    craft(src, crafted, {f"data/{rel}": data for rel, data in EVIL.items()})
    r = run("migrate", str(crafted), str(dest))
    assert r.exit_code == 0, r.output
    for rel in EVIL:
        assert not (dest / rel).exists(), f"{rel} was written"
        assert f"skipped: {rel} — " in r.output, r.output
    assert (dest / "memory" / "web" / "a.md").read_text() == "fact a\n"       # the rest proceeds
    assert git(dest, "status", "--porcelain") == ""
    assert commit_hook_still_commits(dest, tmp_path)


def test_m5_migrate_from_git_archive_never_writes_a_git_component(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    bundle = tar_bundle(tmp_path / "v1.tar", {"projects.conf": b"web:/tmp/web\n",
                                              "memory/web/a.md": b"fact a\n", **EVIL})
    dest = tmp_path / "new"
    r = run("migrate", str(bundle), str(dest))
    assert r.exit_code == 0, r.output
    for rel in EVIL:
        assert not (dest / rel).exists(), f"{rel} was written"
        assert f"skipped: {rel} — " in r.output, r.output
    assert (dest / "memory" / "web" / "a.md").is_file()


# ── M6: the migration commit is scanned ──────────────────────────────────────────────────
def test_m6_uncommitted_secret_is_redacted_before_the_migration_commit(kr, tmp_path):
    (kr / "memory" / "web" / "pending.md").write_text(f"deploy with {AWS} today\n")   # never committed
    src, dest = tmp_path / "x.tar.gz", tmp_path / "new"
    export_to(src)
    r = run("migrate", str(src), str(dest))
    assert r.exit_code == 0, r.output
    committed = git(dest, "show", "HEAD:memory/web/pending.md")
    assert AWS not in committed and MARKER.format(kind="aws-access-key") in committed
    assert AWS not in git(dest, "log", "--all", "-p")
    kept = list((tmp_path / "st" / "corkern" / "quarantine").rglob("pending.md"))
    assert kept and AWS in kept[0].read_text()
    assert "secrets removed from 1 file(s) before the migration commit" in r.output


def test_m6_unscannable_file_with_a_secret_word_is_not_imported(kr, tmp_path):
    blob = b"\xff\xfe binary token " + AWS.encode() + b"\x00"
    (kr / "memory" / "web" / "held.bin").write_bytes(blob)   # the hooks hold such a file back
    src, dest = tmp_path / "x.tar.gz", tmp_path / "new"
    export_to(src)
    r = run("migrate", str(src), str(dest))
    assert r.exit_code == 0, r.output
    assert not (dest / "memory" / "web" / "held.bin").exists()
    assert "skipped: memory/web/held.bin — could not be checked for secrets" in r.output
    assert AWS not in git(dest, "log", "--all", "-p", "--text")


def test_m6_scan_failure_fails_closed(kr, tmp_path, monkeypatch):
    (kr / "memory" / "web" / "pending.md").write_text(f"deploy with {AWS} today\n")
    src, dest = tmp_path / "x.tar.gz", tmp_path / "new"
    export_to(src)

    def broken(*a, **k):
        raise OSError("quarantine not writable")
    monkeypatch.setattr("corkern.commands.secrets_cmd.redact_files", broken)
    r = run("migrate", str(src), str(dest))
    assert r.exit_code == 0, r.output
    assert subprocess.run(["git", "-C", str(dest), "rev-parse", "--verify", "-q", "HEAD"],
                          capture_output=True).returncode != 0, "something was committed unscanned"
    assert (dest / "memory" / "web" / "pending.md").is_file()
    assert "not committed: the secret scan could not run" in r.output


def test_import_skips_a_path_whose_folder_is_a_file_here(kr, tmp_path):
    """A link cloned with core.symlinks=false is a plain file: nothing can be imported below it
    (found by the migrate/3 scenario symlink-remote: the import crashed with FileExistsError)."""
    src = tmp_path / "x.tar.gz"
    export_to(src)
    (kr / "memory" / "web" / "a.md").unlink()
    shutil.rmtree(kr / "memory" / "web")
    (kr / "memory" / "web").write_text("/home/someone/outside-dir")      # what the link became
    r = run("import", str(src), "--apply", "--yes")
    assert r.exit_code == 0, r.output
    assert "Traceback" not in r.output and (kr / "memory" / "web").is_file()
    assert "memory/web/a.md: not corkern data (or behind a symlink or a file)" in r.output


def test_m6_check_after_migrate_counts_redacted_and_held_files_as_expected(kr, tmp_path, monkeypatch):
    """migrate/2-setup-new-machine.sh confirms the migration with `import --check`: a file that
    differs only by the redaction, or was held back as unscannable, is not a mismatch."""
    (kr / "memory" / "web" / "pending.md").write_text(f"deploy with {AWS} today\n")
    (kr / "memory" / "web" / "held.bin").write_bytes(b"\xff\xfe binary token " + AWS.encode())
    src, dest = tmp_path / "x.tar.gz", tmp_path / "new"
    export_to(src)
    assert run("migrate", str(src), str(dest)).exit_code == 0
    monkeypatch.setenv("CORKERN_MEMORY_DIR", str(dest))
    r = run("import", str(src), "--check")
    assert r.exit_code == 0, r.output
    assert "different here" not in r.output and "missing here" not in r.output, r.output
    assert "1 with secrets removed here" in r.output
    assert "skipped: memory/web/held.bin — could not be checked for secrets" in r.output
