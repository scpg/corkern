"""harvest / refine / prune — transcripts in a temporary ~/.claude, a stub `claude -p`."""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corkern import guard
from corkern.commands.harvest_cmd import extract, split_blocks, write_memories
from corkern.main import app

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = """=== FILE: decision_db.md ===
---
name: db-choice
description: Postgres chosen over SQLite for concurrency
metadata:
  type: project
---
Use Postgres.

=== FILE: user_role.md ===
---
name: user-role
description: Senior backend developer
metadata:
  type: user
---
The user is a senior backend developer.
"""
CLAUDE_STUB = """#!/usr/bin/env bash
cat > "$STUB_DIR/last-prompt.txt"
if [ -f "$STUB_DIR/fail" ]; then exit 3; fi
cat "$STUB_DIR/answer.txt"
"""


def line(role, content):
    return json.dumps({"type": role, "message": {"role": role, "content": content}})


TRANSCRIPT = "\n".join([
    line("user", "Let's pick a database. Postgres or SQLite?"),
    line("assistant", [{"type": "text", "text": "Postgres, for concurrent writers."},
                       {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]),
    line("user", [{"type": "tool_result", "content": "noise"}]),
    "not json", ""]) + "\n"


def test_extract_keeps_conversation_text_only():
    text = extract(TRANSCRIPT.encode())
    assert "USER:\nLet's pick a database" in text and "CLAUDE:\nPostgres, for concurrent" in text
    assert "noise" not in text and "ls" not in text
    long = extract(line("user", "x" * 5000).encode())
    assert "[...truncated...]" in long and len(long) < 2200


def test_split_blocks_rejects_unsafe_names():
    out = split_blocks("=== FILE: ok.md ===\nbody\n=== FILE: ../evil.md ===\nx\n=== FILE: .hidden.md ===\nx\n"
                       "=== FILE: notes.txt ===\nx\n=== FILE: empty.md ===\n")
    assert out == [("ok.md", "body")]


def test_write_memories_never_overwrites_and_indexes(tmp_path):
    (tmp_path / "decision_db.md").write_text("existing\n")
    (tmp_path / "MEMORY.md").write_text("# Index\n- [old](old.md)")
    written = write_memories(tmp_path, split_blocks(BLOCKS))
    assert (tmp_path / "decision_db.md").read_text() == "existing\n"
    assert written[0].startswith("decision_db.harvested-") and written[1] == "user_role.md"
    index = (tmp_path / "MEMORY.md").read_text().splitlines()
    assert index[1] == "- [old](old.md)"
    assert index[2] == f"- [db-choice]({written[0]}) — Postgres chosen over SQLite for concurrency"
    assert index[3] == "- [user-role](user_role.md) — Senior backend developer"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = home / "dev" / "my_app.v2"              # '_' and '.' — Claude Code turns them into '-'
    project.mkdir(parents=True)
    kr = tmp_path / "kr"
    assert CliRunner().invoke(app, ["init", str(kr)]).exit_code == 0
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "claude").write_text(CLAUDE_STUB)
    (stub / "claude").chmod(0o755)
    (stub / "answer.txt").write_text(BLOCKS)
    for k, v in {"HOME": str(home), "CORKERN_MEMORY_DIR": str(kr), "STUB_DIR": str(stub),
                 "XDG_STATE_HOME": str(tmp_path / "state"), "CORKERN_DEVICE_NAME": "laptop",
                 "PATH": f"{stub}:{os.environ['PATH']}"}.items():
        monkeypatch.setenv(k, v)
    assert CliRunner().invoke(app, ["add", "app", str(project)]).exit_code == 0
    tdir = home / ".claude" / "projects" / "-".join(["", *str(project).strip("/").replace(".", "/").replace("_", "/").split("/")])
    tdir.mkdir(parents=True)
    assert tdir == guard.transcript_dir(project)
    (tdir / "s1.jsonl").write_text(TRANSCRIPT)
    return {"kr": kr, "stub": stub, "tdir": tdir, "mem": kr / "memory" / "app", "project": project}


def run(*args, stdin=""):
    return CliRunner().invoke(app, list(args), input=stdin)


def test_harvest_writes_memories_and_records_manifest(env):
    r = run("harvest", "app")
    assert r.exit_code == 0, r.output
    assert "New conversations" in r.output and "Wrote 2 memory file(s)" in r.output
    assert (env["mem"] / "decision_db.md").read_text().startswith("---\nname: db-choice")
    assert "[user-role](user_role.md)" in (env["mem"] / "MEMORY.md").read_text()
    prompt = (env["stub"] / "last-prompt.txt").read_text()
    assert "Postgres, for concurrent writers." in prompt and 'project called "app"' in prompt
    manifest = (env["mem"] / ".harvest-manifest").read_text().split("\t")
    assert manifest[0] == "s1.jsonl" and manifest[3].strip() == "laptop"
    again = run("harvest", "app")
    assert "All conversations already harvested" in again.output
    (env["tdir"] / "s1.jsonl").write_text(TRANSCRIPT + line("user", "one more thing") + "\n")
    assert "Changed since last harvest" in run("harvest", "app").output


def test_harvest_lets_the_user_pick(env):
    (env["tdir"] / "s2.jsonl").write_text(line("user", "second conversation") + "\n")
    os.utime(env["tdir"] / "s1.jsonl", (1_700_000_000, 1_700_000_000))
    r = run("harvest", "app", stdin="2\n")
    assert r.exit_code == 0, r.output
    prompt = (env["stub"] / "last-prompt.txt").read_text()
    assert "Conversation: s1.jsonl" in prompt and "s2.jsonl" not in prompt
    assert ".harvest-manifest" and "s2.jsonl" not in (env["mem"] / ".harvest-manifest").read_text()


def test_harvest_all_reprocesses(env):
    run("harvest", "app")
    r = run("harvest", "app", "--all")
    assert "--all: reprocessing" in r.output and "Wrote 2" in r.output


def test_claude_failure_records_nothing(env):
    (env["stub"] / "fail").write_text("")
    r = run("harvest", "app")
    assert r.exit_code == 1 and "nothing was written" in r.output
    assert not (env["mem"] / ".harvest-manifest").exists()


def test_no_memories(env):
    (env["stub"] / "answer.txt").write_text("NO_MEMORIES\n")
    assert "No memories worth persisting" in run("harvest", "app").output


def test_unparseable_answer_is_kept_and_refine_archives_it(env):
    (env["stub"] / "answer.txt").write_text("Here are some thoughts, no blocks.\n")
    r = run("harvest", "app")
    assert "corkern refine app" in r.output
    bulk = list(env["mem"].glob("harvested-*.md"))
    assert len(bulk) == 1
    (env["stub"] / "answer.txt").write_text(BLOCKS)
    r = run("refine", "app")
    assert r.exit_code == 0, r.output
    assert "bulk file moved to archive/" in r.output and not bulk[0].exists()
    assert list((env["mem"] / "archive").glob(f"*-{bulk[0].name}"))
    assert "Here are some thoughts" in (env["stub"] / "last-prompt.txt").read_text()


def test_refine_nothing_to_do(env):
    env["mem"].mkdir(parents=True, exist_ok=True)
    assert "nothing to refine" in run("refine", "app").output


def test_no_transcripts_is_explained(env):
    (env["tdir"] / "s1.jsonl").unlink()
    r = run("harvest", "app")
    assert r.exit_code == 0 and "Has Claude Code been used" in r.output


def test_interactive_prompt_stays_private(env):
    """-i hands the prompt to an interactive Claude session; the file holds transcripts."""
    (env["stub"] / "claude").write_text('#!/usr/bin/env bash\necho "claude $*"\n')
    r = subprocess.run([str(ROOT / ".venv/bin/corkern"), "harvest", "app", "-i"], capture_output=True,
                       text=True, env=os.environ.copy(), timeout=60)
    assert "--append-system-prompt-file" in r.stdout
    prompt_file = Path(os.environ["XDG_STATE_HOME"]) / "corkern" / "harvest-prompt.md"
    assert stat.S_IMODE(prompt_file.stat().st_mode) == 0o600
    assert not list(env["kr"].rglob("*prompt*"))


def test_prune_inventory(env):
    (env["tdir"] / "s2.jsonl").write_text(line("user", "later") + "\n")
    r = run("prune", "app")
    assert r.output.count("pending") >= 2 and "corkern harvest app" in r.output
    run("harvest", "app")
    r = run("prune", "app")
    assert r.output.count("harvested ") >= 2 and "claude project purge" in r.output
    assert "Total: 2 conversations" in r.output


def test_transcripts_are_read_only(env):
    with pytest.raises(ValueError):
        guard.read_transcript(env["kr"] / "corkern.conf")
    before = {p: p.stat().st_mtime for p in env["tdir"].iterdir()}
    run("harvest", "app")
    run("prune", "app")
    assert {p: p.stat().st_mtime for p in env["tdir"].iterdir()} == before
