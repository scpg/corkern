"""Release gate: inspect the built wheel and sdist before anything is uploaded to (Test)PyPI.

    uv build -o dist && .venv/bin/python scripts/release_gate.py dist

Opens every file inside the wheel and the sdist (nothing is installed or executed) and fails
with a report when:

  allow-list     a file lies outside the allow-list (WHEEL_ALLOWED / SDIST_ALLOWED, also used
                 by tests/test_package.py), or an sdist member is not a regular file
  secret         a text file matches one of corkern's own secret patterns (corkern.secret_scan)
  private-marker a text file contains a private marker of the owner's: names, e-mail addresses,
                 host names… stored only as salted SHA-256 hashes in scripts/private_markers.json
                 (add them with scripts/add_private_marker.py)
  knowledge-repo a file belongs to a knowledge repo's data layout (MEMORY.md, .metrics.tsv,
                 corkern.conf, .devices/, memory/, sessions/ …) or a text file holds knowledge
                 repo data (memory frontmatter, memory index lines, registry / manifest lines
                 with a real path). Templates written as Python strings in knowledge_repo.py are
                 code and pass: they carry no data and hold placeholders only.

Lives outside src/corkern so it never ships; standard library plus corkern.secret_scan only.
Reports never print a matched secret or marker: only file, line and the kind of finding.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # the repo's own patterns, installed or not

from corkern import secret_scan  # noqa: E402

MARKERS_FILE = Path(__file__).resolve().parent / "private_markers.json"

# ── allow-list (single source: tests/test_package.py imports these) ─────────────────────────
WHEEL_ALLOWED = ("corkern/", "corkern-")            # package + its .dist-info
SDIST_ALLOWED = ("src/corkern/", "pyproject.toml", "PYPI.md", "PKG-INFO", ".gitignore", "LICENSE")


def wheel_name_allowed(name: str) -> bool:
    return name.startswith(WHEEL_ALLOWED)


def sdist_name_allowed(name: str) -> bool:
    """`name` relative to the sdist's top folder (corkern-<version>/)."""
    return name.startswith(SDIST_ALLOWED)


# ── knowledge repo layout ───────────────────────────────────────────────────────────────────
LAYOUT_FILES = {"memory.md", ".metrics.tsv", "corkern.conf", "corkern.conf.local", ".corkern-format",
                "projects.conf", "claude.md", "todo.md", ".harvest-manifest", ".corkern.lock",
                ".distill-prompt.md"}
LAYOUT_DIRS = {"memory", ".devices", "sessions", "archive", "global"}
LAYOUT_PATTERNS = (re.compile(r"^\.last-sync-"), re.compile(r"\.conflict-[^/]*\.md$"))

LAYOUT_TEXT: list[tuple[str, re.Pattern[str]]] = [
    # a memory file's frontmatter with a concrete type (templates use <user|project|…>)
    ("memory frontmatter", re.compile(r"(?im)^[ \t]*type:[ \t]*(?:user|feedback|project|reference)[ \t]*$")),
    # a MEMORY.md index entry: - [Title](file.md)
    ("memory index entry", re.compile(r"(?m)^[ \t]*- \[[^\]\n]+\]\([^)\s]+\.md\)")),
    # corkern.conf / projects.conf / .devices/<host> line: slug:[stamp:]/absolute/path
    ("registry or manifest line", re.compile(
        r"(?m)^[ \t]*[A-Za-z0-9][A-Za-z0-9._-]*:(?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:)?(?:/|~/)[^\s<>{}$]+[ \t]*$")),
]


def layout_path_problem(path: str) -> str | None:
    parts = [p.lower() for p in PurePosixPath(path).parts]
    if not parts:
        return None
    if parts[-1] in LAYOUT_FILES:
        return f"knowledge repo file name '{PurePosixPath(path).name}'"
    for d in parts[:-1]:
        if d in LAYOUT_DIRS:
            return f"knowledge repo folder '{d}/'"
    for rx in LAYOUT_PATTERNS:
        if rx.search(parts[-1]):
            return "knowledge repo state file"
    return None


# ── private markers ─────────────────────────────────────────────────────────────────────────
_WORD = re.compile(r"[a-z0-9]+")
_COMPOUND = re.compile(r"[a-z0-9]+(?:[._+-][a-z0-9]+)+")
_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
_HOME = re.compile(r"/home/([^/\s'\"`:;,)]+)")


def normalize_marker(marker: str) -> str:
    """How a marker is stored: lower case; several words become single-space separated words."""
    m = marker.strip().lower()
    if re.search(r"\s", m):
        return " ".join(_WORD.findall(m))
    return m


def candidates(text: str) -> set[str]:
    """Every token a marker can match: words, dotted/hyphenated compounds (host names),
    e-mail addresses, /home/<name> and <name>, and 1–3 word windows."""
    t = text.lower()
    out: set[str] = set()
    words = _WORD.findall(t)
    out.update(words)
    for n in (2, 3):
        out.update(" ".join(words[i:i + n]) for i in range(len(words) - n + 1))
    out.update(_COMPOUND.findall(t))
    for e in _EMAIL.findall(t):
        out.add(e)
        out.add(e.split("@", 1)[1])
    for name in _HOME.findall(t):
        out.update((name, f"/home/{name}"))
    return out


def marker_hash(salt: str, token: str) -> str:
    return hashlib.sha256(f"{salt}\0{token}".encode()).hexdigest()


def load_markers(path: Path = MARKERS_FILE) -> tuple[str, set[str]]:
    data = json.loads(path.read_text())
    salt, hashes = data["salt"], data["hashes"]
    if not isinstance(salt, str) or len(salt) < 16:
        raise ValueError(f"{path}: salt missing or too short")
    return salt, set(hashes)


def marker_lines(text: str, salt: str, hashes: set[str]) -> list[int]:
    """Line numbers holding a private marker (never the marker itself)."""
    if not hashes:
        return []
    found = []
    lines = text.splitlines()
    for no, line in enumerate(lines, 1):
        # windows across a line break: the line plus the start of the next
        joined = line + " " + " ".join(lines[no].split()[:2]) if no < len(lines) else line
        if any(marker_hash(salt, c) in hashes for c in candidates(joined)):
            found.append(no)
    return found


# ── the gate ────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Finding:
    artifact: str
    path: str
    check: str
    detail: str

    def __str__(self) -> str:
        return f"  [{self.check}] {self.artifact}: {self.path} — {self.detail}"


def _text(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def check_file(artifact: str, path: str, data: bytes, salt: str, hashes: set[str]) -> list[Finding]:
    found = []
    problem = layout_path_problem(path)
    if problem:
        found.append(Finding(artifact, path, "knowledge-repo", problem))
    text = _text(data)
    if text is None:
        return found
    for hit in secret_scan.scan(text):
        found.append(Finding(artifact, path, "secret", f"line {hit.line(text)}: {hit.kind}"))
    for no in marker_lines(text, salt, hashes):
        found.append(Finding(artifact, path, "private-marker", f"line {no}"))
    for kind, rx in LAYOUT_TEXT:
        for m in rx.finditer(text):
            found.append(Finding(artifact, path, "knowledge-repo",
                                 f"line {text.count(chr(10), 0, m.start()) + 1}: {kind}"))
    return found


def check_wheel(whl: Path, salt: str, hashes: set[str]) -> list[Finding]:
    found = []
    with zipfile.ZipFile(whl) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            if not wheel_name_allowed(info.filename):
                found.append(Finding(whl.name, info.filename, "allow-list", "not allowed in the wheel"))
            found += check_file(whl.name, info.filename, z.read(info), salt, hashes)
    return found


def check_sdist(sdist: Path, salt: str, hashes: set[str]) -> list[Finding]:
    found = []
    with tarfile.open(sdist) as t:
        for m in t.getmembers():
            if m.isdir():
                continue
            rel = m.name.split("/", 1)[1] if "/" in m.name else m.name
            if not m.isfile():
                found.append(Finding(sdist.name, m.name, "allow-list", "not a regular file (link/device)"))
                continue
            if not sdist_name_allowed(rel):
                found.append(Finding(sdist.name, m.name, "allow-list", "not allowed in the sdist"))
            f = t.extractfile(m)
            found += check_file(sdist.name, rel, f.read() if f else b"", salt, hashes)
    return found


def run(dist: Path, markers: Path = MARKERS_FILE) -> tuple[list[Finding], list[str]]:
    """(findings, errors). Errors are structural: no wheel/sdist, unreadable marker file."""
    errors = []
    wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1:
        errors.append(f"expected exactly one wheel in {dist}, found {len(wheels)}")
    if len(sdists) != 1:
        errors.append(f"expected exactly one sdist (*.tar.gz) in {dist}, found {len(sdists)}")
    try:
        salt, hashes = load_markers(markers)
    except (OSError, ValueError, KeyError) as e:
        errors.append(f"private markers: {e}")
        return [], errors
    found: list[Finding] = []
    for w in wheels:
        found += check_wheel(w, salt, hashes)
    for s in sdists:
        found += check_sdist(s, salt, hashes)
    return found, errors


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: release_gate.py DIST_DIR", file=sys.stderr)
        return 2
    dist = Path(argv[0])
    findings, errors = run(dist)
    _, hashes = load_markers() if not errors else ("", set())
    for e in errors:
        print(f"release gate: ERROR {e}")
    if findings:
        print(f"release gate: FAILED — {len(findings)} finding(s):")
        for f in findings:
            print(f)
    if errors or findings:
        return 1
    print(f"release gate: passed (allow-list, secrets, {len(hashes)} private marker hash(es), "
          f"knowledge repo layout) for {', '.join(p.name for p in sorted(dist.iterdir()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
