'''
GBit Shell - test suite
Run with:  python -m pytest tests/ -v
'''
import os
import sys
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gbit_shell.config import ShellConfig, _coerce, _unquote
from gbit_shell.prompt import _short_path
from gbit_shell.github import sanitize_repo_name
from gbit_shell.builtins import BUILTINS, _human_size
from gbit_shell.executor import Executor
from gbit_shell.shell import GBitShell


# ----------------------------------------------------------------------
# Config parsing
# ----------------------------------------------------------------------

def test_coerce_booleans():
    assert _coerce("true") is True
    assert _coerce("false") is False
    assert _coerce("on") is True
    assert _coerce("off") is False
    assert _coerce("42") == 42
    assert _coerce("ocean") == "ocean"


def test_unquote():
    assert _unquote('"ls -la"') == "ls -la"
    assert _unquote("'ls -la'") == "ls -la"
    assert _unquote("ls -la") == "ls -la"


def test_default_aliases_present():
    config = ShellConfig()
    assert "ll" in config.aliases
    assert config.aliases["gs"] == "git status"


def test_alias_expansion():
    config = ShellConfig()
    config.aliases["gs"] = "git status"
    assert config.resolve_alias("gs") == "git status"
    assert config.resolve_alias("gs --short") == "git status --short"
    assert config.resolve_alias("unknowncmd") == "unknowncmd"


def test_alias_no_infinite_recursion():
    config = ShellConfig()
    config.aliases["ls"] = "ls --color"
    # One level of expansion only
    assert config.resolve_alias("ls") == "ls --color"


# ----------------------------------------------------------------------
# Prompt path shortening
# ----------------------------------------------------------------------

def test_short_path_home():
    home = str(Path.home())
    assert _short_path(home) == "~"


def test_short_path_under_home():
    home = str(Path.home())
    assert _short_path(os.path.join(home, "web3-hub")) == "~/web3-hub"


def test_short_path_truncates_deep():
    home = str(Path.home())
    deep = os.path.join(home, "a", "b", "c", "d", "e", "f")
    result = _short_path(deep)
    assert result.startswith("~/")
    assert "…" in result
    assert result.endswith("c/d/e/f")


def test_short_path_absolute():
    assert _short_path("/usr") == "/usr"


# ----------------------------------------------------------------------
# GitHub helpers
# ----------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("meu projeto", "meu-projeto"),
    ("web3 hub!", "web3-hub"),
    ("already-valid", "already-valid"),
    ("com/barra", "com-barra"),
    ("...", "my-project"),
    ("  spaced  ", "spaced"),
    ("a@@@b", "a-b"),
])
def test_sanitize_repo_name(raw, expected):
    assert sanitize_repo_name(raw) == expected


# ----------------------------------------------------------------------
# Builtins registry
# ----------------------------------------------------------------------

def test_core_builtins_registered():
    for name in ("cd", "ls", "pwd", "help", "exit", "ghpush", "serve",
                 "ports", "sysinfo", "theme", "alias"):
        assert name in BUILTINS, f"builtin ausente: {name}"


def test_human_size():
    assert _human_size(0) == "0B"
    assert _human_size(512) == "512B"
    assert _human_size(2048).endswith("K")
    assert _human_size(5 * 1024 * 1024).endswith("M")


# ----------------------------------------------------------------------
# Executor: shell-metacharacter detection
# ----------------------------------------------------------------------

@pytest.fixture
def executor():
    shell = GBitShell(no_banner=True, interactive=False)
    return Executor(shell)


@pytest.mark.parametrize("line", [
    "echo a | grep a",
    "ls > out.txt",
    "cat < in.txt",
    "echo $(date)",
    "ls *.py",
    "npm run build >> log.txt",
])
def test_needs_shell_true(executor, line):
    assert executor._needs_shell(line) is True


@pytest.mark.parametrize("line,expected", [
    ("a && b", [("", "a"), ("&&", "b")]),
    ("a || b", [("", "a"), ("||", "b")]),
    ("a; b", [("", "a"), (";", "b")]),
    ("cd /tmp && ls -la", [("", "cd /tmp"), ("&&", "ls -la")]),
    ("git commit -m 'a; b'", [("", "git commit -m 'a; b'")]),
    ('echo "x && y"', [("", 'echo "x && y"')]),
])
def test_split_sequence(line, expected):
    from gbit_shell.executor import split_sequence
    assert split_sequence(line) == expected


@pytest.mark.parametrize("line", [
    "ls -la",
    "git commit -m 'fix: algo'",
    'echo "texto | com pipe dentro"',
    "ghpush -m 'feat: nova pagina'",
    "python script.py --flag",
])
def test_needs_shell_false(executor, line):
    assert executor._needs_shell(line) is False


# ----------------------------------------------------------------------
# Executor: running commands
# ----------------------------------------------------------------------

def test_run_builtin_pwd(executor, capsys):
    code = executor.run("pwd")
    assert code == 0
    assert os.getcwd() in capsys.readouterr().out


def test_run_empty_line(executor):
    assert executor.run("") == 0
    assert executor.run("   ") == 0
    assert executor.run("# comentario") == 0


def test_run_unknown_command(executor):
    code = executor.run("comando_que_nao_existe_xyz")
    assert code == 127


def test_run_cd_and_back(executor):
    original = os.getcwd()
    try:
        assert executor.run("cd /") == 0
        assert os.getcwd() == os.path.realpath("/") or os.getcwd() == "/"
        assert executor.run("cd -") == 0
        assert os.path.realpath(os.getcwd()) == os.path.realpath(original)
    finally:
        os.chdir(original)


def test_run_cd_nonexistent(executor):
    original = os.getcwd()
    assert executor.run("cd /caminho/que/nao/existe/xyz") == 1
    assert os.getcwd() == original


def test_pipeline_via_shell(executor):
    # Exercises the system-shell path
    code = executor.run("echo gbit | tr a-z A-Z")
    assert code == 0


def test_alias_then_execute(executor):
    executor.shell.config.aliases["mypwd"] = "pwd"
    assert executor.run("mypwd") == 0


# ----------------------------------------------------------------------
# CLI entry point (subprocess, end-to-end)
# ----------------------------------------------------------------------

def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "gbit_shell", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30,
    )


def test_cli_version():
    result = _cli("--version")
    assert result.returncode == 0
    assert "GBit Shell" in result.stdout


def test_cli_help():
    result = _cli("--help")
    assert result.returncode == 0
    assert "gbit" in result.stdout.lower()


def test_cli_single_command():
    result = _cli("-c", "pwd")
    assert result.returncode == 0
    assert str(ROOT) in result.stdout


def test_cli_help_topics():
    for topic in ("config", "github", "keys"):
        result = _cli("-c", f"help {topic}")
        assert result.returncode == 0, f"help {topic} falhou"
        assert len(result.stdout) > 50


def test_cli_sysinfo():
    result = _cli("-c", "sysinfo")
    assert result.returncode == 0
    assert "GBit Shell" in result.stdout


def test_cli_no_banner_flag():
    result = _cli("--no-banner", "-c", "pwd")
    assert result.returncode == 0


def test_cli_ghstatus_outside_repo(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "gbit_shell", "-c", "ghstatus"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert "repositorio git" in result.stdout.lower()


# ----------------------------------------------------------------------
# Filesystem builtins against a temp dir
# ----------------------------------------------------------------------

def test_file_lifecycle(executor, tmp_path):
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert executor.run("mkdir projeto") == 0
        assert (tmp_path / "projeto").is_dir()

        assert executor.run("touch projeto/arquivo.txt") == 0
        assert (tmp_path / "projeto" / "arquivo.txt").is_file()

        assert executor.run("cp projeto/arquivo.txt projeto/copia.txt") == 0
        assert (tmp_path / "projeto" / "copia.txt").is_file()

        assert executor.run("mv projeto/copia.txt projeto/movido.txt") == 0
        assert (tmp_path / "projeto" / "movido.txt").is_file()
        assert not (tmp_path / "projeto" / "copia.txt").exists()

        assert executor.run("rm projeto/movido.txt") == 0
        assert not (tmp_path / "projeto" / "movido.txt").exists()

        # Removing a directory needs -r
        assert executor.run("rm projeto") == 1
        assert executor.run("rm -rf projeto") == 0
        assert not (tmp_path / "projeto").exists()
    finally:
        os.chdir(original)


def test_cat_missing_file(executor):
    assert executor.run("cat /nao/existe/arquivo.txt") == 1


def test_ls_missing_path(executor):
    assert executor.run("ls /nao/existe/xyz") == 1


# ----------------------------------------------------------------------
# Theme handling
# ----------------------------------------------------------------------

def test_theme_switch(executor):
    assert executor.run("theme ocean") == 0
    assert executor.shell.config.get("theme") == "ocean"


def test_theme_invalid(executor):
    assert executor.run("theme naoexiste") == 1


def test_all_themes_build():
    from gbit_shell.styles import get_style, theme_names
    for name in theme_names():
        assert get_style(name) is not None


# ----------------------------------------------------------------------
# Env builtins
# ----------------------------------------------------------------------

def test_export_and_unset(executor):
    assert executor.run("export GBIT_TEST_VAR=hello") == 0
    assert os.environ.get("GBIT_TEST_VAR") == "hello"
    assert executor.run("unset GBIT_TEST_VAR") == 0
    assert "GBIT_TEST_VAR" not in os.environ


def test_env_masks_secrets(executor, capsys):
    os.environ["GBIT_TEST_TOKEN"] = "super-secreto-123"
    try:
        executor.run("env GBIT_TEST")
        out = capsys.readouterr().out
        assert "super-secreto-123" not in out
        assert "oculto" in out
    finally:
        os.environ.pop("GBIT_TEST_TOKEN", None)


def test_alias_define_and_remove(executor):
    assert executor.run("alias teste=echo oi") == 0
    assert executor.shell.config.aliases["teste"] == "echo oi"
    assert executor.run("unalias teste") == 0
    assert "teste" not in executor.shell.config.aliases


# ----------------------------------------------------------------------
# Completer
# ----------------------------------------------------------------------

def test_completer_suggests_builtins():
    from prompt_toolkit.document import Document
    from gbit_shell.completer import GBitCompleter

    config = ShellConfig()
    completer = GBitCompleter(config, BUILTINS.keys())
    doc = Document("ghp", cursor_position=3)
    results = [c.text for c in completer.get_completions(doc, None)]
    assert "ghpush" in results


def test_completer_git_subcommands():
    from prompt_toolkit.document import Document
    from gbit_shell.completer import GBitCompleter

    config = ShellConfig()
    completer = GBitCompleter(config, BUILTINS.keys())
    doc = Document("git comm", cursor_position=8)
    results = [c.text for c in completer.get_completions(doc, None)]
    assert "commit" in results


def test_completer_theme_names():
    from prompt_toolkit.document import Document
    from gbit_shell.completer import GBitCompleter

    config = ShellConfig()
    completer = GBitCompleter(config, BUILTINS.keys())
    doc = Document("theme oc", cursor_position=8)
    results = [c.text for c in completer.get_completions(doc, None)]
    assert "ocean" in results


# ----------------------------------------------------------------------
# Git info
# ----------------------------------------------------------------------

def test_gitinfo_outside_repo(tmp_path):
    from gbit_shell.gitinfo import get_git_info, find_repo_root
    assert find_repo_root(str(tmp_path)) is None
    assert get_git_info(str(tmp_path)) is None


def test_gitinfo_in_real_repo(tmp_path):
    from gbit_shell.gitinfo import get_git_info, invalidate_cache
    if not subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
        pytest.skip("git nao disponivel")

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, env=env)
    (repo / "a.txt").write_text("conteudo")

    invalidate_cache()
    info = get_git_info(str(repo))
    assert info is not None
    assert info["branch"] == "main"
    assert info["untracked"] >= 1
    assert info["dirty"] is True
    assert info["has_remote"] is False


def test_gbit_builtin_shows_banner(capsys):
    """`gbit` and `gbit shell` inside the session force the banner."""
    from gbit_shell.builtins import BUILTINS
    from gbit_shell.shell import GBitShell
    s = GBitShell(no_banner=True, interactive=False)
    assert "gbit" in BUILTINS
    code = BUILTINS["gbit"]([], s)
    assert code == 0
    out = capsys.readouterr().out
    assert "GBIT" in out or "TERMINAL-SHELL" in out


def test_gbit_shell_alias(capsys):
    """`gbit shell` should also show the banner."""
    from gbit_shell.builtins import BUILTINS
    from gbit_shell.shell import GBitShell
    s = GBitShell(no_banner=True, interactive=False)
    code = BUILTINS["gbit"](["shell"], s)
    assert code == 0
    out = capsys.readouterr().out
    assert "GBIT" in out or "TERMINAL-SHELL" in out


def test_gbit_help_flag(capsys):
    """`gbit --help` should show help text."""
    from gbit_shell.builtins import BUILTINS
    from gbit_shell.shell import GBitShell
    s = GBitShell(no_banner=True, interactive=False)
    code = BUILTINS["gbit"](["--help"], s)
    assert code == 0
    out = capsys.readouterr().out
    assert "banner" in out.lower() or "gbit" in out.lower()
