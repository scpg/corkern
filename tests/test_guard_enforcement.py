"""Structural test: guard.py is the only module allowed to touch the filesystem.

Any direct file I/O, subprocess, or shutil use elsewhere in src/corkern fails the build,
so new code cannot bypass the v1 isolation guard by accident.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "corkern"
GUARD = SRC / "guard.py"

FORBIDDEN_METHODS = {
    "open", "read_text", "read_bytes", "write_text", "write_bytes", "mkdir", "touch",
    "unlink", "rmdir", "rename", "replace", "symlink_to", "hardlink_to", "chmod",
    "iterdir", "glob", "rglob", "walk",
}
FORBIDDEN_MODULES = {"shutil", "subprocess", "tempfile", "io", "pty", "fileinput"}
FORBIDDEN_OS_CALLS = {
    "system", "popen", "remove", "unlink", "rmdir", "removedirs", "makedirs", "mkdir",
    "rename", "renames", "replace", "symlink", "link", "chmod", "chown", "truncate",
    "listdir", "scandir", "walk", "open", "fdopen", "execv", "execvp", "spawnv",
}


def _violations(path: Path) -> list[str]:
    found = []
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        loc = f"{path.name}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_MODULES:
                    found.append(f"{loc} import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in FORBIDDEN_MODULES:
                found.append(f"{loc} from {node.module} import ...")
            if mod == "os":
                for a in node.names:
                    if a.name in FORBIDDEN_OS_CALLS:
                        found.append(f"{loc} from os import {a.name}")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in {"open", "exec", "eval", "__import__"}:
                found.append(f"{loc} {f.id}()")
            elif isinstance(f, ast.Attribute):
                if isinstance(f.value, ast.Name) and f.value.id == "os":
                    if f.attr in FORBIDDEN_OS_CALLS:
                        found.append(f"{loc} os.{f.attr}()")
                elif isinstance(f.value, ast.Name) and f.value.id == "guard":
                    continue  # the sanctioned path
                elif f.attr in FORBIDDEN_METHODS:
                    found.append(f"{loc} .{f.attr}()")
    return found


def test_only_guard_touches_filesystem():
    violations = []
    for py in sorted(SRC.rglob("*.py")):
        if py != GUARD:
            violations += _violations(py)
    assert not violations, "Direct filesystem access outside guard.py:\n" + "\n".join(violations)


def test_scanner_detects_violations(tmp_path):
    # Sanity check: the scanner itself must catch the patterns it claims to.
    bad = tmp_path / "probe.py"
    bad.write_text(
        "import subprocess\nimport os\nfrom pathlib import Path\n"
        "Path('x').write_text('y')\nopen('x')\nos.remove('x')\n"
        "from corkern import guard\nguard.write_text('x', 'ok')\n"
    )
    v = _violations(bad)
    assert len(v) == 4, v  # guard.write_text is allowed
