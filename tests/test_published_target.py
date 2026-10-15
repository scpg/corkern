"""`make test-published` tests the package from PyPI / TestPyPI, never this checkout, and can never
reach the developer's real files: the image holds only the test scripts and no host folder is
mounted (the knowledge repos it syncs are created inside the container)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "docker" / "Dockerfile.published").read_text()
MAKEFILE = (ROOT / "Makefile").read_text()


def _target(name: str) -> str:
    match = re.search(rf"^{name}:.*?(?=^\S|\Z)", MAKEFILE, re.M | re.S)
    assert match, name
    return match.group(0)


def test_image_holds_only_the_test_scripts():
    copies = [line for line in DOCKERFILE.splitlines() if line.startswith(("COPY", "ADD"))]
    sources = [part for line in copies if "--from=" not in line for part in line.split()[1:-1]]
    assert sources == ["docker/e2e/lib.sh", "docker/published/"], sources
    assert "pip install ." not in DOCKERFILE and "uv build" not in DOCKERFILE


def test_no_host_folder_is_mounted():
    target = _target("test-published")
    assert "docker run" in target
    assert not re.search(r"(\s-v\s|--volume|--mount|\$\(CURDIR\)|\$HOME|~/)", target), target


def test_corkern_comes_only_from_the_index():
    run = (ROOT / "docker" / "published" / "run.sh").read_text()
    assert 'uv tool install -q --refresh --index-url https://pypi.org/simple/ "$SPEC"' in run
    assert 'SPEC="corkern==$VERSION"' in run and "--wheel-to" in run       # TestPyPI: the checked wheel
    assert "extra-index-url" not in run and "unsafe-best-match" not in run   # never mix the indexes
    assert "install.sh" not in run and "/src" not in run.replace('"no corkern source', "")
