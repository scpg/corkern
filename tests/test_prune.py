"""memory-prune — v2 safety behaviour beyond v1 parity (see test_parity_v1 for parity)."""
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
C2 = ROOT / ".venv" / "bin" / "corkern"


@pytest.fixture
def kr(tmp_path):
    repo = tmp_path / "kr"
    subprocess.run([str(C2), "init", str(repo)], check=True, capture_output=True)
    mem = repo / "memory" / "p"
    (mem / "sessions").mkdir(parents=True)
    (mem / "archive").mkdir()
    for path in ("old.md", "sessions/s.md", "archive/a.md", "MEMORY.md", "todo.md"):
        (mem / path).write_text("---\nexpires: 2020-01-01\n---\n")
    return repo


def prune(repo, *args, stdin=""):
    return subprocess.run([str(C2), "memory-prune", "p", *args], input=stdin, capture_output=True,
                          text=True, env={**os.environ, "CORKERN_MEMORY_DIR": str(repo)})


def test_skips_index_todo_sessions_and_archive(kr):
    r = prune(kr, "--dry-run")
    assert "1 candidate(s)" in r.stdout and "old.md" in r.stdout


def test_no_answer_means_no(kr):
    r = prune(kr)                                        # stdin closed, no --yes
    assert "Aborted. Nothing moved." in r.stdout
    assert (kr / "memory/p/old.md").exists()


def test_yes_flag_archives(kr):
    prune(kr, "--yes")
    stamp = date.today().strftime("%Y%m%d")
    assert (kr / f"memory/p/archive/{stamp}-old.md").exists()
    assert not (kr / "memory/p/old.md").exists()


def test_never_overwrites_an_archived_file(kr):
    stamp = date.today().strftime("%Y%m%d")
    (kr / f"memory/p/archive/{stamp}-old.md").write_text("earlier archive")
    prune(kr, "--yes")
    assert (kr / f"memory/p/archive/{stamp}-old.md").read_text() == "earlier archive"
    assert (kr / f"memory/p/archive/{stamp}-1-old.md").exists()


def test_expires_today_counts_as_expired(kr):
    (kr / "memory/p/today.md").write_text(f"---\nexpires: {date.today():%Y-%m-%d}\n---\n")
    assert "today.md" in prune(kr, "--dry-run").stdout
