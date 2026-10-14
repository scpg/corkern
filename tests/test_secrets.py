"""Secrets never leave the machine: detection rules, `corkern secrets`, and the hooks."""
import os
import re
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corkern.main import app
from corkern.secret_scan import HINT, MARKER, redact, scan
from tests.test_hooks import ROOT, git, hook, world, write_memory  # noqa: F401  (fixture)

# Built at runtime so this file itself never contains a literal token
GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
SAMPLES = {
    "github-token": f"push with {GH} please",
    "anthropic-key": "ANTHROPIC_API_KEY=" + "sk-ant-" + "api03-" + "Zx9" * 10,  # gitleaks:allow (synthetic)
    "openai-key": "key " + "sk-proj-" + "Ab1" * 12,
    "aws-access-key": "aws " + "AKIA" + "ABCDEFGHIJ234567",
    "slack-token": "xoxb-" + "123456789012-abcdefABCDEF",
    "google-api-key": "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
    "gitlab-token": "glpat-" + "x1Y2z3A4b5C6d7E8f9G0",
    "npm-token": "npm_" + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8",
    "private-key": "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAA\n-----END OPENSSH "
                   + "PRIVATE KEY-----",
    "assigned-credential": "DB_PASSWORD=" + "hunter2Secret99",
    # a Google OAuth client secret WITHOUT any digit: the generic assignment rule needs a digit,
    # so only the dedicated pattern catches it (found by the teams simulation, CI 2026-10-14)
    "google-oauth-secret": '{"installed": {"client_secret": "' + "GOCSPX-" + "abcdefghijklmnopqrstuvwxyzAB" + '"}}',
}
HARMLESS = [
    "token: GH_TOKEN_TESTING (only via tmp.local/gh-test)",
    "api_key: ${API_KEY}",
    "password: see 1Password",
    "secret_key = <your-secret-key-here>",
    "the token file lives at /home/me/.config/token1234567890",
    "Use a deploy key; never print the .env token.",
    MARKER.format(kind="github-token"),
]


@pytest.mark.parametrize("kind", SAMPLES)
def test_known_secrets_are_found_and_redacted(kind):
    text = f"before\n{SAMPLES[kind]}\nafter\n"
    hits = scan(text)
    assert [h.kind for h in hits] == [kind]
    redacted, _ = redact(text)
    assert MARKER.format(kind=kind) in redacted
    assert scan(redacted) == []                      # idempotent: the marker is not a secret
    assert redacted.startswith("before\n") and redacted.endswith("\nafter\n")


@pytest.mark.parametrize("text", HARMLESS)
def test_harmless_text_is_left_alone(text):
    assert scan(text) == []


def test_assignment_keeps_the_key_name():
    out, _ = redact("db_password: 'Tr0ub4dor&3xyz'\n")
    assert out == "db_password: '" + MARKER.format(kind="assigned-credential") + "'\n"


def test_google_oauth_secret_without_a_key_name():
    """No 'secret' word nearby: the bash pre-filter (HINT) must still let it through to Python."""
    text = "oauth for the CLI: " + "GOCSPX-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZab" + "\n"
    assert [h.kind for h in scan(text)] == ["google-oauth-secret"]
    assert re.search(HINT, text, re.I)


def test_hint_prefilter_catches_every_sample():
    for text in SAMPLES.values():
        assert re.search(HINT, text, re.I), text


def test_bash_prefilter_matches_python():
    lib = (ROOT / "src" / "corkern" / "hooks" / "lib.sh").read_text()
    bash_hint = re.search(r"^CK_SECRET_HINT='([^']*)'", lib, re.M).group(1)
    assert bash_hint == HINT


@pytest.fixture
def repo(tmp_path, monkeypatch):
    kr = tmp_path / "kr"
    assert CliRunner().invoke(app, ["init", str(kr)]).exit_code == 0
    monkeypatch.setenv("CORKERN_MEMORY_DIR", str(kr))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.chdir(kr)
    return kr


def test_redact_keeps_original_privately(repo, tmp_path):
    f = repo / "memory" / "p" / "notes.md"
    f.parent.mkdir(parents=True)
    f.write_text(f"deploy token {GH}\n")
    r = CliRunner().invoke(app, ["secrets", "--redact", "memory/p/notes.md"])
    assert r.exit_code == 0, r.output
    assert r.output.strip() == "memory/p/notes.md"
    assert GH not in f.read_text() and "secret removed by corkern" in f.read_text()
    kept = list((tmp_path / "state" / "corkern" / "quarantine").rglob("notes.md"))
    assert len(kept) == 1 and GH in kept[0].read_text()
    assert stat.S_IMODE(kept[0].stat().st_mode) == 0o600
    listing = CliRunner().invoke(app, ["secrets"])
    assert "memory/p/notes.md" in listing.output and "github-token" in listing.output


def test_redact_refuses_files_outside_the_repo(repo, tmp_path):
    outside = tmp_path / "other.md"
    outside.write_text(f"{GH}\n")
    r = CliRunner().invoke(app, ["secrets", "--redact", str(outside)])
    assert r.exit_code == 0 and GH in outside.read_text()


def test_scan_reports_files_and_history(repo):
    f = repo / "memory" / "p" / "a.md"
    f.parent.mkdir(parents=True)
    f.write_text(f"{GH}\n")
    git(repo, "add", "-A")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "oops")
    f.write_text("clean now\n")
    r = CliRunner().invoke(app, ["secrets", "--scan"])
    assert r.exit_code == 0, r.output                # files are clean …
    r = CliRunner().invoke(app, ["secrets", "--scan", "--history"])
    assert r.exit_code == 1 and "github-token" in r.output   # … but history is not
    assert "rotate" in r.output


class TestHooks:
    def test_secret_in_a_memory_never_reaches_the_remote(self, world):
        write_memory(world["a"], "proj/creds.md", f"use {GH} for the API\npassword = Sup3rSecretPw!\n")
        hook("corkern-commit-memory", world["a"], world["tmp"])
        hook("corkern-push", world["a"], world["tmp"])
        pushed = git(world["remote"], "log", "-p", "--all")
        assert "creds.md" in pushed
        assert GH not in pushed and "Sup3rSecretPw" not in pushed
        on_disk = (world["a"] / "memory/proj/creds.md").read_text()
        assert "secret removed by corkern: github-token" in on_disk
        kept = list((world["tmp"] / "state" / "corkern" / "quarantine").rglob("creds.md"))
        assert kept and GH in kept[0].read_text()

    def test_env_and_key_files_are_never_staged(self, world):
        write_memory(world["a"], "proj/.env", "API_KEY=abc123def456ghi\n")  # gitleaks:allow (synthetic)
        write_memory(world["a"], "proj/id_ed25519", "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nx\n")
        write_memory(world["a"], "proj/ok.md", "fine\n")
        hook("corkern-commit-memory", world["a"], world["tmp"])
        tracked = git(world["a"], "ls-files")
        assert "memory/proj/ok.md" in tracked
        assert ".env" not in tracked and "id_ed25519" not in tracked

    def test_clean_memories_do_not_start_python(self, world):
        write_memory(world["a"], "proj/plain.md", "nothing to see\n")
        r = hook("corkern-commit-memory", world["a"], world["tmp"],
                 extra={"PATH": "/usr/bin:/bin", "HOME": str(world["tmp"] / "nohome")})
        assert r.returncode == 0
        assert "memory/proj/plain.md" in git(world["a"], "ls-files")

    def test_without_corkern_a_suspicious_file_is_not_committed(self, world):
        write_memory(world["a"], "proj/creds.md", f"{GH}\n")
        hook("corkern-commit-memory", world["a"], world["tmp"],
             extra={"PATH": "/usr/bin:/bin", "HOME": str(world["tmp"] / "nohome")})
        assert "creds.md" not in git(world["a"], "ls-files")
        assert GH not in git(world["a"], "log", "-p", "--all")
        note = (world["tmp"] / "state" / "corkern" / "last-hook-problem").read_text()
        assert "could not check" in note and "corkern secrets --scan" in note


def test_session_context_mentions_recently_removed_secrets(world):
    write_memory(world["a"], "proj/creds.md", f"{GH}\n")
    hook("corkern-commit-memory", world["a"], world["tmp"])
    out = hook("corkern-session-start", world["a"], world["tmp"]).stdout
    assert "secrets removed before commit" in out and "memory/proj/creds.md" in out
    assert GH not in out


def test_unreadable_suspicious_file_is_held_back(world):
    blob = b"\x89PNG\x00\x01" + f" token={GH} ".encode() + b"\xff\xfe" * 10
    p = world["a"] / "memory" / "proj" / "shot.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(blob)
    write_memory(world["a"], "proj/fine.md", "fine\n")
    hook("corkern-commit-memory", world["a"], world["tmp"])
    tracked = git(world["a"], "ls-files")
    assert "memory/proj/fine.md" in tracked and "shot.png" not in tracked
    note = (world["tmp"] / "state" / "corkern" / "last-hook-problem").read_text()
    assert "shot.png could not be checked" in note
