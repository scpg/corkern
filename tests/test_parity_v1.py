"""Parity: the real v1 `corkern` script vs corkern, run on identical data.

The v1 script comes from `git show v1/main:corkern` (remote `v1` = the private v1 repository, local only;
CI has no v1 code, so these tests skip there) and runs
from a temporary directory with a temporary HOME — the installed v1 is never touched.
Skipped when origin/main is not available (CI fetches it explicitly).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import confirm

ROOT = Path(__file__).resolve().parents[1]
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _v1_script() -> str | None:
    ref = os.environ.get("CORKERN_V1_REF", "v1/main")
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:corkern"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 and r.stdout.startswith("#!") else None


V1 = _v1_script()
pytestmark = pytest.mark.skipif(V1 is None, reason="v1 code not available (git fetch v1; local only)")

CONF = "# slug:path\nalpha:dev/alpha\nbeta:dev/beta\nhome:\n"
TODO = "## Sprint\n- [ ] 2026-09-01 first\n- [x] 2026-09-02 old\n## Backlog\n- [ ] 2026-09-03 second\n"
PRUNE = {"expired.md": "metadata:\n  expires: 2020-01-01\n", "stale.md": "metadata:\n  status: stale\n",
         "fresh.md": "metadata:\n  expires: 2999-01-01\n", "plain.md": ""}
SESSIONS = {"2026-09-01-1000.md": "- older note\n", "2026-09-02-1000.md": "- newer note\n- next\n"}


@pytest.fixture
def pair(tmp_path):
    """Identical data laid out as a v1 repo and as a v2 knowledge repo."""
    home = tmp_path / "home"
    for p in ("dev/alpha", "dev/beta"):
        (home / p).mkdir(parents=True)
    v1, v2 = tmp_path / "v1", tmp_path / "v2"
    for root, conf_name in ((v1, "projects.conf"), (v2, "corkern.conf")):
        (root / "memory" / "alpha" / "sessions").mkdir(parents=True)
        (root / "memory" / "global").mkdir(parents=True)
        (root / conf_name).write_text(CONF)
        (root / "memory/alpha/todo.md").write_text(TODO)
        (root / "memory/alpha/x.md").write_text("x\n")
        for name, front in PRUNE.items():
            (root / "memory/alpha" / name).write_text(f"---\nname: {name}\n{front}---\nbody\n")
        for i, (name, text) in enumerate(SESSIONS.items()):
            f = root / "memory/alpha/sessions" / name
            f.write_text(text)
            os.utime(f, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed alpha"], check=True)
    (v1 / "corkern").write_text(V1)
    (v1 / "corkern").chmod(0o755)
    # v2 uses a shared registry's paths only once this machine confirmed them (v1 has no such
    # step): here, the user registered these projects on this machine
    confirm(v2, {"alpha": home / "dev/alpha", "beta": home / "dev/beta", "home": home})
    return {"home": home, "v1": v1, "v2": v2}


def run_both(pair, args, cwd=None, stdin=""):
    env = {**os.environ, "HOME": str(pair["home"]), "NO_COLOR": "1", "TERM": "dumb"}
    env.pop("CORKERN_DIR", None)
    cwd = cwd or pair["home"]
    r1 = subprocess.run([str(pair["v1"] / "corkern"), *args], cwd=cwd, env=env, input=stdin,
                        capture_output=True, text=True, timeout=30)
    v2_bin = shutil.which("corkern", path=str(ROOT / ".venv" / "bin")) or "corkern"
    r2 = subprocess.run([v2_bin, *args], cwd=cwd, env={**env, "CORKERN_MEMORY_DIR": str(pair["v2"])},
                        input=stdin, capture_output=True, text=True, timeout=30)
    norm = lambda s: ANSI.sub("", s).replace("corkern ", "corkern ").rstrip()
    return (r1.returncode == 0, norm(r1.stdout), norm(r1.stderr)), \
           (r2.returncode == 0, norm(r2.stdout), norm(r2.stderr))


def same_files(pair, rel):
    a, b = pair["v1"] / rel, pair["v2"] / rel
    assert a.exists() == b.exists(), rel
    if a.exists():
        assert a.read_text() == b.read_text(), rel


CASES = [
    (["todos", "alpha"], None),
    (["todos"], "dev/alpha"),            # current project + global
    (["todos"], None),                   # untracked dir → global only
    (["todos", "--all"], None),
    (["todo", "alpha"], None),           # v1 alias
    (["sessions", "alpha"], None),
    (["sessions"], "dev/alpha"),
    (["sessions", "beta"], None),        # no notes
    (["done", "9", "alpha"], None),      # not found
    (["done", "1", "gamma"], None),      # no todo file
]


@pytest.mark.parametrize("args,cwd", CASES, ids=lambda x: " ".join(x) if isinstance(x, list) else str(x))
def test_read_only_commands_match_v1(pair, args, cwd):
    v1, v2 = run_both(pair, args, cwd and pair["home"] / cwd)
    assert v2 == v1


@pytest.mark.parametrize("args,cwd,files", [
    (["remind", "ship it", "alpha"], None, ["memory/alpha/todo.md"]),
    (["remind", "from cwd"], "dev/alpha", ["memory/alpha/todo.md"]),
    (["remind", "to global"], None, ["memory/global/todo.md"]),
    (["remind", "new project", "beta"], None, ["memory/beta/todo.md"]),
    (["done", "2", "alpha"], None, ["memory/alpha/todo.md"]),
    (["done", "1"], "dev/alpha", ["memory/alpha/todo.md"]),
], ids=lambda x: " ".join(x) if isinstance(x, list) else str(x))
def test_writing_commands_match_v1(pair, args, cwd, files):
    v1, v2 = run_both(pair, args, cwd and pair["home"] / cwd)
    assert v2 == v1
    for rel in files:
        same_files(pair, rel)
    v1, v2 = run_both(pair, ["todos", "--all"])   # and the follow-up view agrees too
    assert v2 == v1


def test_log_lists_same_commits(pair):
    (v1_ok, v1_out, _), (v2_ok, v2_out, _) = run_both(pair, ["log", "alpha"])
    strip = lambda s: [re.sub(r"^[0-9a-f]{7,} ", "", line) for line in s.split("\n")]
    assert v1_ok and v2_ok and strip(v2_out) == strip(v1_out)


def tree(root, rel):
    base = root / rel
    return sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())


@pytest.mark.parametrize("args,stdin", [
    (["memory-prune", "alpha", "--dry-run"], ""),
    (["memory-prune", "alpha"], "y\n"),
    (["memory-prune", "alpha"], "n\n"),
    (["memory-prune", "global"], ""),            # nothing to prune
    (["memory-prune", "nosuch"], ""),            # no memory dir
], ids=["dry-run", "yes", "no", "nothing", "missing"])
def test_memory_prune_matches_v1(pair, args, stdin):
    v1, v2 = run_both(pair, args, stdin=stdin)
    known = {  # deliberate v2 differences: its own repo path; an accurate commit hint
        str(pair["v2"]): str(pair["v1"]),
        "(The next corkern hook run — end of a Claude reply or session start — commits it.)":
            "(PostToolUse hook will commit the changes automatically.)",
    }
    out = v2[1]
    for mine, theirs in known.items():
        out = out.replace(mine, theirs)
    assert (v2[0], out, v2[2]) == v1
    assert tree(pair["v2"], "memory") == tree(pair["v1"], "memory")


def _metrics_rows():
    from datetime import date, timedelta
    d = lambda days: f"{date.today() - timedelta(days=days):%Y-%m-%d} 10:00:00"
    rows = [(40, 10, 1000, 2000, 0), (20, 50, 300000, 2000, 0), (5, 100, 400000, 2000, 0),
            (0, 160, 600000, 210000, 26)]
    return "date\thost\tslug\tmem_files\tmem_bytes\tai_bytes\tcontext_pct\n" + "".join(
        f"{d(a)}\thostA\talpha\t{f}\t{b}\t{ai}\t{c}\n" for a, f, b, ai, c in rows)


@pytest.mark.parametrize("content", ["", "header-only", "rows"])
def test_metrics_matches_v1(pair, content):
    if content:
        text = _metrics_rows() if content == "rows" else _metrics_rows().split("\n")[0] + "\n"
        for root in (pair["v1"], pair["v2"]):
            (root / ".metrics.tsv").write_text(text)
    v1, v2 = run_both(pair, ["metrics"])
    assert v2 == v1


def test_add_without_slug_uses_folder_name_like_v1(pair):
    (pair["home"] / "dev" / "gamma").mkdir()
    run_both(pair, ["add"], cwd=pair["home"] / "dev" / "gamma", stdin="y\n")
    line = lambda root, name: [l for l in (root / name).read_text().split("\n") if l.startswith("gamma:")]
    assert line(pair["v2"], "corkern.conf") == line(pair["v1"], "projects.conf") == ["gamma:dev/gamma"]


def test_sessions_newest_by_name_even_when_mtimes_lie(pair):
    """v2 improvement: after a clone/pull mtimes are checkout times; names are the truth."""
    import os
    folder = pair["v2"] / "memory/alpha/sessions"
    for i, name in enumerate(["2026-01-01-0900.md", "2026-12-31-2359.md", "2026-06-15-1200.md"]):
        (folder / name).write_text(f"- {name}\n")
        os.utime(folder / name, (2_000_000_000 - i * 1000, 2_000_000_000 - i * 1000))  # reversed
    env = {**os.environ, "CORKERN_MEMORY_DIR": str(pair["v2"])}
    out = subprocess.run([str(ROOT / ".venv/bin/corkern"), "sessions", "alpha"], env=env,
                         capture_output=True, text=True).stdout
    order = [line.strip() for line in out.split("\n") if line.strip().startswith("2026-")]
    assert order == sorted(order, reverse=True)


def _with_devices(pair):
    """Manifests for this device and a second one, same in both repos."""
    import socket
    me = socket.gethostname().split(".")[0]
    home = pair["home"]
    for root in (pair["v1"], pair["v2"]):
        d = root / ".devices"
        d.mkdir(exist_ok=True)
        (d / me).write_text("# corkern device manifest — auto-generated\n# format: slug:last-seen:path\n"
                            f"host:{me}\nalpha:2026-09-30 10:00:00:{home}/dev/alpha\n"
                            f"beta:2026-09-29 09:00:00:{home}/dev/beta\nhome:2026-09-28 08:00:00:{home}\n")
        (d / "laptop2").write_text("host:laptop2\nalpha:2026-10-01 07:00:00:/Users/x/work/alpha\n"
                                   "home:2026-09-01 12:00:00:/Users/x\n")
        (d / "oldbox").write_text("alpha:/srv/alpha\nupdated:2026-01-02 03:04:05\n")   # old v1 format
    return pair


@pytest.mark.parametrize("args", [
    ["list", "-c", "SLUG,DEVICE,LAST-SEEN"],
    ["list", "-s", "DEVICE", "-c", "SLUG,DEVICE,LAST-SEEN"],
    ["list", "-s", "last-seen", "-s", "slug", "-c", "slug,device,last-seen"],
    ["list", "-s", "PATH", "-c", "SLUG,DEVICE"],
    ["ls", "-s", "PATH", "-c", "DEVICE,SLUG"],
])
def test_list_with_devices_matches_v1(pair, args):
    """Left out by design: STATUS of this device's rows (v1: symlink, v2: autoMemoryDirectory)
    and long PATHs (v1 cuts them at 44 characters, v2 shows them whole)."""
    v1, v2 = run_both(_with_devices(pair), args)
    assert v2 == v1


def test_list_status_column_for_other_devices_matches_v1(pair):
    v1, v2 = run_both(_with_devices(pair), ["list", "-c", "DEVICE,STATUS", "-s", "DEVICE"])
    other = lambda out: [line for line in out[1].splitlines() if "(local)" not in line]
    assert other(v2) == other(v1)


def test_list_paths_of_other_devices_match_v1(pair):
    v1, v2 = run_both(_with_devices(pair), ["list", "-c", "DEVICE,PATH", "-s", "PATH"])
    rows = lambda out: [line.split() for line in out[1].splitlines()
                        if line.startswith(("  laptop2", "  oldbox"))]
    assert rows(v2) == rows(v1) and len(rows(v1)) == 3


def test_prune_inventory_matches_v1(pair):
    """Same conversations and harvest manifest → same rows (name, modified, status, harvested by).
    SIZE differs by design: v1 shows disk usage (`du -h`, 4.0K for a tiny file), v2 the size."""
    import hashlib
    import json as _json
    home = pair["home"]
    project = home / "dev" / "alpha"
    msgs = "\n".join(_json.dumps({"message": {"role": r, "content": c}}) for r, c in (("user", "hi"), ("assistant", "hello")))
    v1_key = str(project).replace("/", "-")
    v2_key = re.sub(r"[^A-Za-z0-9]", "-", str(project))
    for key in {v1_key, v2_key}:
        d = home / ".claude" / "projects" / key
        d.mkdir(parents=True, exist_ok=True)
        for i, name in enumerate(("a.jsonl", "b.jsonl", "c.jsonl")):
            (d / name).write_text(msgs + f"\n{name}\n")
            os.utime(d / name, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))
    digest = hashlib.sha256((msgs + "\na.jsonl\n").encode()).hexdigest()
    manifest = f"a.jsonl\t{digest}\t2026-09-01 10:00\tlaptop\nb.jsonl\tstalehash\t2026-09-02 11:00\tdesktop\n"
    for root in (pair["v1"], pair["v2"]):
        (root / "memory/alpha/.harvest-manifest").write_text(manifest)
    v1, v2 = run_both(pair, ["prune", "alpha"])
    rows = lambda out: [line.split()[:2] + line.split()[3:] for line in out[1].splitlines()
                        if re.match(r"^\s+\d+\.\s", line)]
    assert rows(v2) == rows(v1) and len(rows(v1)) == 3
    assert [r[4] for r in rows(v1)] == ["pending", "changed", "harvested"]
