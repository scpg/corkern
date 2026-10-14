"""scripts/release_gate.py — each check fails a hand-made wheel/sdist holding one bad file.

Fake secrets are assembled at runtime (no literal token in the repo for gitleaks to flag);
private markers are throwaway values hashed into a temporary markers file.
"""
import io
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import add_private_marker  # noqa: E402
import release_gate as gate  # noqa: E402

GOOD = {"corkern/__init__.py": "VERSION = 1\n", "corkern/hooks/lib.sh": "#!/bin/bash\ntrue\n"}
FAKE_GH = "ghp_" + "Q7w8E9r0T1y2U3i4O5p6" * 2


@pytest.fixture
def markers(tmp_path):
    path = tmp_path / "markers.json"
    path.write_text(json.dumps({"salt": "0123456789abcdef0123", "hashes": []}))
    return path


def build(dist: Path, wheel: dict[str, str] | None = None, sdist: dict[str, str] | None = None) -> Path:
    """A wheel and an sdist with GOOD content plus the given extra files."""
    dist.mkdir(exist_ok=True)
    with zipfile.ZipFile(dist / "corkern-9.9.9-py3-none-any.whl", "w") as z:
        for name, text in {**GOOD, "corkern-9.9.9.dist-info/METADATA": "Name: corkern\n", **(wheel or {})}.items():
            z.writestr(name, text)
    with tarfile.open(dist / "corkern-9.9.9.tar.gz", "w:gz") as t:
        files = {f"src/{k}": v for k, v in GOOD.items()} | {"PKG-INFO": "Name: corkern\n", **(sdist or {})}
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"corkern-9.9.9/{name}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return dist


def checks(dist: Path, markers: Path) -> set[tuple[str, str]]:
    findings, errors = gate.run(dist, markers)
    assert not errors, errors
    return {(f.check, f.path) for f in findings}


def test_clean_packages_pass(tmp_path, markers):
    assert checks(build(tmp_path / "dist"), markers) == set()


def test_structural_errors(tmp_path, markers):
    (tmp_path / "empty").mkdir()
    _, errors = gate.run(tmp_path / "empty", markers)
    assert any("wheel" in e for e in errors) and any("sdist" in e for e in errors)


# a. allow-list ──────────────────────────────────────────────────────────────────────────────
def test_file_outside_the_allow_list_fails(tmp_path, markers):
    dist = build(tmp_path / "dist", wheel={"tests/test_x.py": "x = 1\n"}, sdist={"tmp.local/notes.txt": "hi\n"})
    assert checks(dist, markers) == {("allow-list", "tests/test_x.py"),
                                     ("allow-list", "corkern-9.9.9/tmp.local/notes.txt")}


def test_sdist_symlink_fails(tmp_path, markers):
    dist = build(tmp_path / "dist")
    sdist = dist / "corkern-9.9.9.tar.gz"
    with tarfile.open(sdist, "r:gz") as t:
        members = [(m, t.extractfile(m).read() if m.isfile() else None) for m in t.getmembers()]
    with tarfile.open(sdist, "w:gz") as t:
        for m, data in members:
            t.addfile(m, io.BytesIO(data) if data is not None else None)
        link = tarfile.TarInfo("corkern-9.9.9/src/corkern/evil")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        t.addfile(link)
    assert ("allow-list", "corkern-9.9.9/src/corkern/evil") in checks(dist, markers)


def test_allow_list_rules():
    assert gate.wheel_name_allowed("corkern/main.py")
    assert gate.wheel_name_allowed("corkern-2.0.0.dist-info/RECORD")
    assert not gate.wheel_name_allowed("docs/x.md")
    assert gate.sdist_name_allowed("src/corkern/main.py") and gate.sdist_name_allowed("PKG-INFO")
    assert not gate.sdist_name_allowed("tests/test_x.py") and not gate.sdist_name_allowed("docker/x")


# b. secrets ─────────────────────────────────────────────────────────────────────────────────
def test_secret_in_a_text_file_fails(tmp_path, markers):
    dist = build(tmp_path / "dist", wheel={"corkern/x.py": f"TOKEN_NOTE = 'push with {FAKE_GH}'\n"},
                 sdist={"src/corkern/x.md": f"use {FAKE_GH}\n"})
    assert checks(dist, markers) == {("secret", "corkern/x.py"), ("secret", "src/corkern/x.md")}


def test_report_never_prints_the_secret(tmp_path, markers):
    dist = build(tmp_path / "dist", wheel={"corkern/x.py": f"t = '{FAKE_GH}'\n"})
    findings, _ = gate.run(dist, markers)
    assert findings and all(FAKE_GH not in str(f) for f in findings)


def test_binary_files_are_skipped_for_text_checks(tmp_path, markers):
    dist = build(tmp_path / "dist")
    with zipfile.ZipFile(dist / "corkern-9.9.9-py3-none-any.whl", "a") as z:
        z.writestr("corkern/blob.bin", b"\0\1" + FAKE_GH.encode())
    assert checks(dist, markers) == set()


# c. private markers ─────────────────────────────────────────────────────────────────────────
THROWAWAY = ["Zorblax Quentinworth", "zq-laptop.example.internal", "zorblax@example.org", "qwtzx"]


@pytest.mark.parametrize("text, path", [
    ("# Written by ZORBLAX   Quentinworth\n", "corkern/a.py"),          # 2-word window, case, spaces
    ("# Zorblax\n# Quentinworth\n", "corkern/a.py"),                     # window across a line break
    ("HOST = 'zq-laptop.example.internal'\n", "corkern/b.py"),           # host-like token
    ("mail me: <Zorblax@Example.org>\n", "corkern/c.md"),               # e-mail
    ("cd /home/qwtzx/dev\n", "corkern/hooks/d.sh"),                      # /home/<name>
])
def test_private_marker_fails(tmp_path, markers, text, path):
    assert add_private_marker.add(THROWAWAY, markers) == (4, 0, 0)
    dist = build(tmp_path / "dist", wheel={path: text})
    assert ("private-marker", path) in checks(dist, markers)


def test_markers_are_stored_only_as_hashes(markers):
    add_private_marker.add(THROWAWAY, markers)
    stored = markers.read_text().lower()
    for m in THROWAWAY:
        for part in m.lower().split():
            assert part not in stored
    data = json.loads(stored)
    assert len(data["hashes"]) == 4 and all(len(h) == 64 for h in data["hashes"])


def test_markers_do_not_match_other_text(tmp_path, markers):
    add_private_marker.add(THROWAWAY, markers)
    dist = build(tmp_path / "dist", wheel={"corkern/a.py": "# zorblax-free quentin worth zq laptop\n"})
    assert checks(dist, markers) == set()


def test_unmatchable_markers_are_refused_and_duplicates_counted(markers):
    assert add_private_marker.add(["one two three four", "o'brien", "Zorblax"], markers) == (1, 0, 2)
    assert add_private_marker.add(["zorblax"], markers) == (0, 1, 0)


def test_cli_reads_stdin_and_never_echoes(markers, monkeypatch, capsys):
    monkeypatch.setattr(gate, "MARKERS_FILE", markers)
    monkeypatch.setattr(sys, "stdin", io.StringIO("Zorblax Quentinworth\n\nqwtzx\n"))
    assert add_private_marker.main() == 0
    out = capsys.readouterr()
    assert "2 added" in out.out and "zorblax" not in (out.out + out.err).lower()


def test_shipped_markers_file_has_a_salt_and_no_hashes():
    data = json.loads((ROOT / "scripts" / "private_markers.json").read_text())
    assert len(data["salt"]) >= 16
    assert isinstance(data["hashes"], list)


# d. knowledge repo layout ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", [
    "corkern/MEMORY.md", "corkern/data/.metrics.tsv", "corkern/corkern.conf", "corkern/.devices/host1",
    "corkern/memory/proj/note.md", "corkern/x/sessions/2026-01-01-0000.md", "corkern/global/CLAUDE.md",
    "corkern/x/todo.md", "corkern/note.conflict-host-1200.md",
])
def test_knowledge_repo_files_fail(tmp_path, markers, path):
    dist = build(tmp_path / "dist", wheel={path: "x\n"})
    assert ("knowledge-repo", path) in checks(dist, markers)


@pytest.mark.parametrize("text", [
    "---\nname: thing\ndescription: d\ntype: feedback\n---\nbody\n",            # memory file
    "- [Merge rules](feedback_merge.md) — when to merge\n",                     # MEMORY.md index
    "myproj:/srv/code/myproj\n",                                                 # corkern.conf
    "myproj:2026-01-01 10:00:00:/srv/code/myproj\n",                            # .devices manifest
])
def test_knowledge_repo_data_in_text_fails(tmp_path, markers, text):
    dist = build(tmp_path / "dist", sdist={"src/corkern/data.md": text})
    assert ("knowledge-repo", "src/corkern/data.md") in checks(dist, markers)


def test_templates_in_code_pass(tmp_path, markers):
    """knowledge_repo.py's templates and harvest's placeholder format are code, not data."""
    code = ('TEMPLATE = {"memory/global/MEMORY.md": "# Global — Memory Index\\n",\n'
            '            ".gitattributes": "memory/**/MEMORY.md merge=union\\n"}\n'
            'FORMAT = """\\n  type: <user|project|feedback|reference>\\n    <slug>:<path>\\n"""\n')
    dist = build(tmp_path / "dist", wheel={"corkern/knowledge_repo.py": code})
    assert checks(dist, markers) == set()


# CLI ───────────────────────────────────────────────────────────────────────────────────────
def test_cli_exit_codes(tmp_path):
    clean = build(tmp_path / "clean")
    bad = build(tmp_path / "bad", wheel={"corkern/MEMORY.md": "x\n"})
    script = str(ROOT / "scripts" / "release_gate.py")
    ok = subprocess.run([sys.executable, script, str(clean)], capture_output=True, text=True)
    fail = subprocess.run([sys.executable, script, str(bad)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert fail.returncode == 1 and "knowledge-repo" in fail.stdout
