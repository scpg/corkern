"""corkern.conf edge cases from /code-review — each reproduced through the CLI first."""
from pathlib import Path

import pytest
from tests.conftest import list_row, listed_here  # noqa: F401
from typer.testing import CliRunner

from corkern.main import app

runner = CliRunner()


@pytest.fixture
def env(test_repo, tmp_path):
    home = tmp_path / "home"
    (home / "dev" / "p").mkdir(parents=True)
    return {"CORKERN_MEMORY_DIR": str(test_repo), "HOME": str(home)}


def run(args, env):
    return runner.invoke(app, args, env=env)


def conf(env) -> str:
    return (Path(env["CORKERN_MEMORY_DIR"]) / "corkern.conf").read_text()


def append(env, text, name="corkern.conf"):
    p = Path(env["CORKERN_MEMORY_DIR"]) / name
    p.write_text((p.read_text() if p.exists() else "") + text)


class TestLineBreaks:
    @pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c", "\n", "\r"])
    def test_add_rejects_any_line_separator(self, env, sep):
        result = run(["add", "ok", f"/tmp/a{sep}evil:/x"], env)
        assert result.exit_code != 0
        assert "evil" not in conf(env)

    def test_parser_splits_on_newline_only(self, env):
        # A unicode separator already inside a line must not create a phantom entry (v1 reads by \n)
        append(env, "ok:/tmp/a\u2028evil:/x\n")
        out = run(["list"], env).output
        slugs = [line.split()[0] for line in out.split("\n") if line.strip()]
        assert "evil" not in slugs


class TestTilde:
    def test_unknown_user_tilde_does_not_crash(self, env):
        append(env, "proj:~nosuchuser/x\n")
        result = run(["list"], env)
        assert result.exit_code == 0, result.output
        assert f"{env['HOME']}/~nosuchuser/x" in result.output  # v1: relative to HOME

    def test_plain_tilde_is_home(self, env):
        append(env, "t:~/dev/p\n")
        assert listed_here(run(["list"], env).output, "t")


class TestRemove:
    def test_removes_all_device_lines_and_says_so(self, env):
        append(env, "p:C:/Users/me/p\np:dev/p\n")
        result = run(["remove", "p"], env)
        assert result.exit_code == 0
        assert "^p:" not in conf(env) and "\np:" not in conf(env)
        assert "2 lines" in result.output

    def test_local_only_slug_is_not_silently_removed(self, env):
        append(env, "x:/a\n", "corkern.conf.local")
        result = run(["remove", "x"], env)
        assert result.exit_code != 0
        assert "corkern.conf.local" in result.output


class TestAddChecks:
    def test_windows_only_slug_counts_as_existing(self, env):
        append(env, "w:C:/x\n")
        result = run(["add", "w", "/opt/w"], env)
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_same_path_under_two_slugs_refused(self, env):
        p = f"{env['HOME']}/dev/p"
        assert run(["add", "a", p], env).exit_code == 0
        result = run(["add", "b", p], env)
        assert result.exit_code != 0
        assert "already tracked" in result.output and "'a'" in result.output


class TestRoundTrip:
    def test_symlinked_home_stays_relative(self, env, tmp_path):
        real = tmp_path / "real-home"
        (real / "dev" / "q").mkdir(parents=True)
        link = tmp_path / "linkhome"
        link.symlink_to(real)
        e = {**env, "HOME": str(link)}
        assert run(["add", "q", str(link / "dev" / "q")], e).exit_code == 0
        assert "q:dev/q\n" in conf(e)

    @pytest.mark.parametrize("name", ["C:stuff", "~tmp"])
    def test_tricky_names_under_home_round_trip(self, env, name):
        d = Path(env["HOME"]) / name
        d.mkdir()
        assert run(["add", "t", str(d)], env).exit_code == 0
        assert listed_here(run(["list"], env).output, "t")

    def test_trailing_whitespace_path_refused(self, env):
        d = Path(env["HOME"]) / "proj "
        d.mkdir()
        result = run(["add", "w", str(d)], env)
        assert result.exit_code != 0
        assert "whitespace" in result.output


class TestInvalidSlugsInFile:
    def test_skipped_with_warning(self, env):
        append(env, "../x:dev/x\n.hidden:dev/h\n")
        result = run(["list"], env)
        assert result.exit_code == 0
        assert "../x" in result.output and "skipping" in result.output.lower()
        assert list_row(result.output, "../x") is None


class TestNoTracebacks:
    def test_init_under_a_file(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        result = runner.invoke(app, ["init", str(f / "kr")])
        assert result.exit_code != 0
        assert "not a directory" in result.output

    def test_main_turns_unexpected_errors_into_clean_message(self, monkeypatch, capsys):
        from corkern import main as main_mod

        def boom():
            raise RuntimeError("kaboom")
        monkeypatch.setattr(main_mod, "app", boom)
        monkeypatch.delenv("CORKERN_DEBUG", raising=False)
        with pytest.raises(SystemExit) as exc:
            main_mod.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "kaboom" in err and "Traceback" not in err and "CORKERN_DEBUG" in err
