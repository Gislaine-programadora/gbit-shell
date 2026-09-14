'''
GBit Shell - tests for job control and per-project config (gbit.json)
Run with:  python -m pytest tests/ -v
'''
import os
import sys
import json
import time
import signal
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gbit_shell.jobs import (JobManager, RUNNING, STOPPED, DONE, FAILED,
                             POSIX, wait_allow_stop, popen_kwargs)
from gbit_shell.project import (ProjectConfig, load_project, find_project_file,
                                default_template, detect_project_kind,
                                write_project_file, PROJECT_FILE)
from gbit_shell.builtins import BUILTINS
from gbit_shell.shell import GBitShell

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def shell():
    return GBitShell(no_banner=True, interactive=False)


def _spawn(seconds=30):
    """Start a long-lived child suitable for job-control tests."""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **popen_kwargs(background=True)
    )


# ======================================================================
# JobManager
# ======================================================================

def test_job_ids_increment():
    manager = JobManager()
    first = _spawn()
    second = _spawn()
    try:
        job_a = manager.add(first, "sleep a", background=True)
        job_b = manager.add(second, "sleep b", background=True)
        assert (job_a.id, job_b.id) == (1, 2)
        assert manager.current == 2
        assert manager.previous == 1
    finally:
        manager.terminate_all()


def test_job_spec_resolution():
    manager = JobManager()
    first, second = _spawn(), _spawn()
    try:
        manager.add(first, "npm run dev", background=True)
        manager.add(second, "python server.py", background=True)
        assert manager.get("%1").command == "npm run dev"
        assert manager.get("2").command == "python server.py"
        assert manager.get(None).id == 2          # current
        assert manager.get("%+").id == 2
        assert manager.get("%-").id == 1          # previous
        assert manager.get("npm").id == 1         # prefix match
        assert manager.get("%99") is None
    finally:
        manager.terminate_all()


def test_job_poll_marks_finished():
    manager = JobManager()
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    job = manager.add(proc, "noop", background=True)
    proc.wait()
    finished = manager.poll()
    assert finished == [job]
    assert job.state == DONE
    assert job.exit_code == 0
    assert manager.active() == []


def test_job_poll_marks_failure():
    manager = JobManager()
    proc = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    job = manager.add(proc, "boom", background=True)
    proc.wait()
    manager.poll()
    assert job.state == FAILED
    assert job.exit_code == 3


def test_job_prune_forgets_reported():
    manager = JobManager()
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    job = manager.add(proc, "noop", background=True)
    proc.wait()
    manager.poll()
    job.reported = True
    manager.prune()
    assert manager.jobs == {}
    assert manager.current is None


def test_terminate_all_kills_children():
    manager = JobManager()
    proc = _spawn()
    manager.add(proc, "sleep", background=True)
    manager.terminate_all()
    assert proc.poll() is not None


@pytest.mark.skipif(not POSIX, reason="stop signals only exist on POSIX")
def test_suspend_and_resume_roundtrip():
    manager = JobManager()
    proc = _spawn()
    job = manager.add(proc, "sleep", background=True)
    try:
        os.kill(proc.pid, signal.SIGSTOP)
        manager.mark_stopped(job)
        assert job.state == STOPPED
        assert manager.has_stopped()

        assert manager.resume(job, background=True) is True
        assert job.state == RUNNING
        assert proc.poll() is None
    finally:
        manager.terminate_all()


@pytest.mark.skipif(not POSIX, reason="WUNTRACED only exists on POSIX")
def test_wait_allow_stop_detects_stop():
    proc = _spawn()
    try:
        os.kill(proc.pid, signal.SIGSTOP)
        kind, value = wait_allow_stop(proc)
        assert kind == "stopped"
        assert value == signal.SIGTSTP or value == signal.SIGSTOP
    finally:
        os.kill(proc.pid, signal.SIGCONT)
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.skipif(not POSIX, reason="WUNTRACED only exists on POSIX")
def test_wait_allow_stop_detects_exit():
    proc = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"],
                            **popen_kwargs())
    kind, value = wait_allow_stop(proc)
    assert kind == "exited"
    assert value == 7
    assert proc.returncode == 7


def test_popen_kwargs_shape():
    kwargs = popen_kwargs(background=True)
    if POSIX:
        assert callable(kwargs["preexec_fn"])
    else:
        assert "creationflags" in kwargs


def test_popen_kwargs_foreground_no_cnpg():
    """On Windows, foreground jobs must NOT use CREATE_NEW_PROCESS_GROUP."""
    if POSIX:
        # On POSIX, foreground and background both use preexec_fn
        kwargs_fg = popen_kwargs(background=False)
        assert callable(kwargs_fg["preexec_fn"])
    else:
        import subprocess
        kwargs_fg = popen_kwargs(background=False)
        cnpg = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        # Foreground should have flags 0 (same console group as parent)
        assert kwargs_fg["creationflags"] == 0
        # Background should have CREATE_NEW_PROCESS_GROUP
        kwargs_bg = popen_kwargs(background=True)
        assert kwargs_bg["creationflags"] & cnpg


# ======================================================================
# jobs / fg / bg / kill builtins
# ======================================================================

def test_builtins_registered():
    for name in ("jobs", "fg", "bg", "kill", "wait", "disown",
                 "project", "run"):
        assert name in BUILTINS


def test_jobs_empty(shell, capsys):
    assert BUILTINS["jobs"]([], shell) == 0
    assert "nenhum job" in capsys.readouterr().out


def test_background_job_registers(shell, capsys):
    code = shell.executor.run(f"{sys.executable} -c 'import time; time.sleep(20)' &")
    assert code == 0
    assert len(shell.jobs.active()) == 1
    BUILTINS["jobs"]([], shell)
    output = capsys.readouterr().out
    assert "rodando" in output
    shell.jobs.terminate_all()


def test_background_job_finishes_and_is_reported(shell, capsys):
    shell.executor.run(f"{sys.executable} -c 'pass' &")
    job = shell.jobs.active()[0]
    job.proc.wait()
    shell.reap_jobs()
    assert "concluido" in capsys.readouterr().out
    assert shell.jobs.active() == []


def test_builtin_cannot_run_in_background(shell, capsys):
    assert shell.executor.run("pwd &") == 1
    assert "segundo plano" in capsys.readouterr().out


def test_fg_without_jobs(shell, capsys):
    code = BUILTINS["fg"]([], shell)
    assert code == 1


def test_kill_requires_target(shell, capsys):
    assert BUILTINS["kill"]([], shell) == 1
    assert "uso:" in capsys.readouterr().out


def test_kill_lists_signals(shell, capsys):
    assert BUILTINS["kill"](["-l"], shell) == 0
    assert "SIGTERM" in capsys.readouterr().out


def test_kill_job_by_spec(shell):
    shell.executor.run(f"{sys.executable} -c 'import time; time.sleep(20)' &")
    job = shell.jobs.active()[0]
    assert BUILTINS["kill"](["%1"], shell) == 0
    job.proc.wait(timeout=5)
    assert job.proc.poll() is not None


def test_kill_unknown_job(shell, capsys):
    assert BUILTINS["kill"](["%42"], shell) == 1
    assert "nao encontrado" in capsys.readouterr().out


def test_kill_invalid_signal(shell, capsys):
    assert BUILTINS["kill"](["-NOPE", "%1"], shell) == 1
    assert "invalido" in capsys.readouterr().out


def test_wait_with_nothing(shell, capsys):
    assert BUILTINS["wait"]([], shell) == 0
    assert "nada para aguardar" in capsys.readouterr().out


def test_wait_for_background_job(shell):
    shell.executor.run(f"{sys.executable} -c 'pass' &")
    assert BUILTINS["wait"]([], shell) == 0
    assert shell.jobs.active() == []


def test_disown_removes_job_without_killing(shell, capsys):
    shell.executor.run(f"{sys.executable} -c 'import time; time.sleep(20)' &")
    job = shell.jobs.active()[0]
    assert BUILTINS["disown"](["%1"], shell) == 0
    assert "removido" in capsys.readouterr().out
    assert shell.jobs.active() == []
    assert job.proc.poll() is None          # still alive
    job.proc.kill()
    job.proc.wait(timeout=5)


def test_disown_all(shell):
    shell.executor.run(f"{sys.executable} -c 'import time; time.sleep(20)' &")
    shell.executor.run(f"{sys.executable} -c 'import time; time.sleep(20)' &")
    procs = [j.proc for j in shell.jobs.active()]
    assert BUILTINS["disown"](["-a"], shell) == 0
    assert len(shell.jobs) == 0
    for proc in procs:
        assert proc.poll() is None
        proc.kill()
        proc.wait(timeout=5)


def test_disown_unknown_job(shell, capsys):
    assert BUILTINS["disown"](["%42"], shell) == 1
    assert "nao encontrado" in capsys.readouterr().out


# ======================================================================
# set builtin
# ======================================================================

def test_set_lists_options(shell, capsys):
    assert BUILTINS["set"]([], shell) == 0
    out = capsys.readouterr().out
    assert "show_node" in out and "theme" in out


def test_set_toggles_show_node(shell, capsys):
    assert BUILTINS["set"](["show_node=false"], shell) == 0
    assert shell.config.get("show_node") is False
    assert BUILTINS["set"](["show_node=true"], shell) == 0
    assert shell.config.get("show_node") is True


def test_set_reads_single_option(shell, capsys):
    BUILTINS["set"](["tag=API"], shell)
    capsys.readouterr()
    assert BUILTINS["set"](["tag"], shell) == 0
    assert "API" in capsys.readouterr().out


def test_set_rejects_unknown_option(shell, capsys):
    assert BUILTINS["set"](["nao_existe=1"], shell) == 1
    assert "desconhecida" in capsys.readouterr().out


def test_set_rejects_unknown_theme(shell, capsys):
    assert BUILTINS["set"](["theme=roxo"], shell) == 1
    assert "desconhecido" in capsys.readouterr().out


def test_set_save_writes_rc(shell, capsys, tmp_path, monkeypatch):
    rc = tmp_path / ".gbitrc"
    rc.write_text("set show_node=true\nalias ll=ls -la\n", encoding="utf-8")
    monkeypatch.setattr("gbit_shell.config.RC_FILE", rc)
    assert BUILTINS["set"](["--save", "show_node=false"], shell) == 0
    text = rc.read_text(encoding="utf-8")
    assert "set show_node=false" in text
    assert text.count("show_node") == 1        # replaced, not duplicated
    assert "alias ll=ls -la" in text


def test_env_override_hides_node_badge(monkeypatch):
    from gbit_shell.config import ShellConfig
    monkeypatch.setenv("GBIT_SHOW_NODE", "false")
    assert ShellConfig().get("show_node") is False


def test_node_badge_off_by_default():
    from gbit_shell.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["show_node"] is False


def test_prompt_hides_node_badge_by_default(tmp_path, monkeypatch):
    import gbit_shell.config as config_mod
    from gbit_shell.prompt import build_prompt
    monkeypatch.delenv("GBIT_SHOW_NODE", raising=False)
    monkeypatch.setattr(config_mod, "RC_FILE", tmp_path / "absent.gbitrc")
    workdir = tmp_path / "app"          # a neutral name: tmp_path itself
    workdir.mkdir()                     # carries the test name ("...node...")
    (workdir / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(workdir)
    config = config_mod.ShellConfig()
    text = "".join(part[1] for part in build_prompt("/tmp/app", config))
    assert "node" not in text
    config.set("show_node", True)
    text = "".join(part[1] for part in build_prompt("/tmp/app", config))
    assert "node" in text


def test_exit_warns_about_stopped_jobs(shell, capsys):
    proc = _spawn()
    job = shell.jobs.add(proc, "sleep", background=True)
    shell.jobs.mark_stopped(job)
    try:
        assert BUILTINS["exit"]([], shell) == 1        # first attempt warns
        assert "suspenso" in capsys.readouterr().out
        assert shell.running is True
        assert BUILTINS["exit"]([], shell) == 0        # second attempt exits
        assert shell.running is False
    finally:
        shell.jobs.terminate_all()


# ======================================================================
# gbit.json discovery and parsing
# ======================================================================

def test_no_project_config(tmp_path):
    config = load_project(str(tmp_path))
    assert config.exists is False
    assert config.aliases == {}
    assert config.scripts == {}


def test_project_walks_up(tmp_path):
    (tmp_path / "gbit.json").write_text(json.dumps({"name": "raiz"}))
    deep = tmp_path / "src" / "components" / "ui"
    deep.mkdir(parents=True)
    found = find_project_file(str(deep))
    assert found == tmp_path / "gbit.json"
    assert load_project(str(deep)).name == "raiz"


def test_project_nearest_wins(tmp_path):
    (tmp_path / "gbit.json").write_text(json.dumps({"name": "fora"}))
    inner = tmp_path / "pacote"
    inner.mkdir()
    (inner / "gbit.json").write_text(json.dumps({"name": "dentro"}))
    assert load_project(str(inner)).name == "dentro"


def test_project_fields(tmp_path):
    data = {
        "name": "web3-hub",
        "tag": "WEB3",
        "theme": "ocean",
        "aliases": {"dev": "npm run dev"},
        "env": {"NODE_ENV": "development", "PORT": 3000},
        "scripts": {"build": "npm run build",
                    "deploy": ["npm run build", "ghpush"]},
    }
    (tmp_path / "gbit.json").write_text(json.dumps(data))
    config = load_project(str(tmp_path))
    assert config.exists
    assert config.name == "web3-hub"
    assert config.tag == "WEB3"
    assert config.theme == "ocean"
    assert config.aliases == {"dev": "npm run dev"}
    assert config.env == {"NODE_ENV": "development", "PORT": "3000"}
    assert config.scripts["build"] == ["npm run build"]
    assert config.scripts["deploy"] == ["npm run build", "ghpush"]


def test_project_invalid_json_is_reported(tmp_path):
    (tmp_path / "gbit.json").write_text("{ isto nao e json")
    config = load_project(str(tmp_path))
    assert config.exists
    assert config.error is not None
    assert config.aliases == {}


def test_project_rejects_non_object(tmp_path):
    (tmp_path / "gbit.json").write_text("[1, 2, 3]")
    config = load_project(str(tmp_path))
    assert config.error is not None


def test_project_ignores_unknown_keys(tmp_path):
    (tmp_path / "gbit.json").write_text(json.dumps(
        {"name": "x", "onLoad": "rm -rf /", "hooks": ["curl evil.sh"]}))
    config = load_project(str(tmp_path))
    assert "onLoad" not in config.data
    assert "hooks" not in config.data


def test_project_name_defaults_to_folder(tmp_path):
    folder = tmp_path / "meu-repo"
    folder.mkdir()
    (folder / "gbit.json").write_text("{}")
    assert load_project(str(folder)).name == "meu-repo"


def test_project_prompt_overrides(tmp_path):
    (tmp_path / "gbit.json").write_text(json.dumps(
        {"show_git": False, "prompt_style": "minimal"}))
    config = load_project(str(tmp_path))
    overrides = config.prompt_overrides()
    assert overrides["show_git"] is False
    assert overrides["prompt_style"] == "minimal"


def test_write_project_file_refuses_overwrite(tmp_path):
    written, _ = write_project_file(str(tmp_path), {"name": "a"})
    assert written is True
    again, message = write_project_file(str(tmp_path), {"name": "b"})
    assert again is False
    assert "ja existe" in message
    forced, _ = write_project_file(str(tmp_path), {"name": "b"}, force=True)
    assert forced is True
    assert json.loads((tmp_path / "gbit.json").read_text())["name"] == "b"


def test_detect_project_kind_node(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    detected = detect_project_kind(str(tmp_path))
    assert detected["aliases"]["dev"] == "npm run dev"


def test_detect_project_kind_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    detected = detect_project_kind(str(tmp_path))
    assert "pytest" in detected["scripts"]["test"]


def test_default_template_shape():
    template = default_template("minha-app", {"aliases": {"dev": "x"}, "scripts": {}})
    assert template["name"] == "minha-app"
    # a tag do prompt sempre nasce "GBIT" simples, sem derivar (e cortar)
    # o nome do projeto — evita tags feias tipo "GBIT-SHE" pra "gbit-shell"
    assert template["tag"] == "GBIT"
    assert template["aliases"]["dev"] == "x"
    assert "publish" in template["scripts"]


def test_legacy_gbit_shell_tag_is_normalized(tmp_path):
    from gbit_shell.config import ShellConfig
    from gbit_shell.prompt import build_prompt

    config = ShellConfig()
    config.set("tag", "GBIT-SHE")
    rendered = "".join(text for _style, text in build_prompt(str(tmp_path), config))
    assert " GBIT " in rendered
    assert "GBIT-SHE" not in rendered


# ======================================================================
# Shell integration with gbit.json
# ======================================================================

def test_shell_loads_project_on_cd(shell, tmp_path):
    (tmp_path / "gbit.json").write_text(json.dumps(
        {"name": "proj", "aliases": {"oi": "pwd"}}))
    origin = os.getcwd()
    try:
        shell.executor.run(f"cd {tmp_path}")
        assert shell.project.exists
        assert shell.project.name == "proj"
        assert "oi" in shell.all_aliases()
        assert shell.executor.run("oi") == 0
    finally:
        os.chdir(origin)


def test_project_alias_overrides_global(shell, tmp_path):
    shell.config.aliases["dev"] = "echo global"
    (tmp_path / "gbit.json").write_text(json.dumps(
        {"aliases": {"dev": "echo projeto"}}))
    origin = os.getcwd()
    try:
        shell.executor.run(f"cd {tmp_path}")
        assert shell.all_aliases()["dev"] == "echo projeto"
        assert shell.resolve_alias("dev") == "echo projeto"
    finally:
        os.chdir(origin)


def test_alias_loop_is_broken(shell):
    shell.project = load_project(os.path.join(os.sep, "nonexistent-gbit-dir"))
    shell.config.aliases["a"] = "b"
    shell.config.aliases["b"] = "a"
    resolved = shell.resolve_alias("a")
    assert resolved in ("a", "b")


def test_project_env_reaches_children(shell, tmp_path):
    (tmp_path / "gbit.json").write_text(json.dumps(
        {"name": "envtest", "env": {"GBIT_TEST_VAR": "123"}}))
    origin = os.getcwd()
    try:
        shell.executor.run(f"cd {tmp_path}")
        env = shell.executor._child_env()
        assert env["GBIT_TEST_VAR"] == "123"
        assert env["GBIT_PROJECT"] == "envtest"
        assert env["GBIT_PROJECT_ROOT"] == str(tmp_path.resolve())
    finally:
        os.chdir(origin)


def test_project_init_builtin(shell, tmp_path):
    origin = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert BUILTINS["project"](["init"], shell) == 0
        assert (tmp_path / "gbit.json").is_file()
        assert BUILTINS["project"](["init"], shell) == 1     # already exists
        assert BUILTINS["project"]([], shell) == 0            # info
        assert BUILTINS["project"](["scripts"], shell) == 0
        assert BUILTINS["project"](["path"], shell) == 0
        assert BUILTINS["project"](["reload"], shell) == 0
    finally:
        os.chdir(origin)


def test_project_info_without_file(shell, tmp_path, capsys):
    origin = os.getcwd()
    try:
        os.chdir(tmp_path)
        shell._project_dir = None
        shell.refresh_project(announce=False)
        assert BUILTINS["project"]([], shell) == 1
        assert "project init" in capsys.readouterr().out
    finally:
        os.chdir(origin)


# ======================================================================
# run <script>
# ======================================================================

def test_run_lists_scripts(shell, tmp_path, capsys):
    (tmp_path / "gbit.json").write_text(json.dumps(
        {"scripts": {"ola": "pwd", "tchau": "pwd"}}))
    origin = os.getcwd()
    try:
        shell.executor.run(f"cd {tmp_path}")
        assert BUILTINS["run"]([], shell) == 0
        output = capsys.readouterr().out
        assert "ola" in output and "tchau" in output
    finally:
        os.chdir(origin)


def test_run_unknown_script(shell, tmp_path, capsys):
    (tmp_path / "gbit.json").write_text(json.dumps({"scripts": {"ola": "pwd"}}))
    origin = os.getcwd()
    try:
        shell.executor.run(f"cd {tmp_path}")
        assert BUILTINS["run"](["nada"], shell) == 1
        assert "nao encontrado" in capsys.readouterr().out
    finally:
        os.chdir(origin)


def test_run_executes_single_step(shell, tmp_path):
    marker = tmp_path / "feito.txt"
    (tmp_path / "gbit.json").write_text(json.dumps(
        {"scripts": {"tocar": "touch feito.txt"}}))
    origin = os.getcwd()
    try:
        shell.executor.run(f"cd {tmp_path}")
        assert BUILTINS["run"](["tocar"], shell) == 0
        assert marker.is_file()
    finally:
        os.chdir(origin)


def test_run_multi_step_stops_on_failure(shell, tmp_path, capsys):
    (tmp_path / "gbit.json").write_text(json.dumps(
        {"scripts": {"cadeia": ["comando-que-nao-existe-xyz", "touch nunca.txt"]}}))
    origin = os.getcwd()
    try:
        shell.executor.run(f"cd {tmp_path}")
        code = BUILTINS["run"](["cadeia"], shell)
        assert code != 0
        assert not (tmp_path / "nunca.txt").exists()
    finally:
        os.chdir(origin)


def test_run_uses_project_root(shell, tmp_path):
    (tmp_path / "gbit.json").write_text(json.dumps(
        {"scripts": {"tocar": "touch na-raiz.txt"}}))
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    origin = os.getcwd()
    try:
        shell.executor.run(f"cd {deep}")
        assert BUILTINS["run"](["tocar"], shell) == 0
        assert (tmp_path / "na-raiz.txt").is_file()
        assert Path(os.getcwd()).resolve() == deep.resolve()
    finally:
        os.chdir(origin)


def test_run_without_project(shell, tmp_path, capsys):
    origin = os.getcwd()
    try:
        os.chdir(tmp_path)
        shell._project_dir = None
        shell.refresh_project(announce=False)
        assert BUILTINS["run"]([], shell) == 1
    finally:
        os.chdir(origin)


# ======================================================================
# Prompt and completer
# ======================================================================

def test_prompt_shows_project_and_jobs(tmp_path):
    from gbit_shell.config import ShellConfig
    from gbit_shell.prompt import build_prompt

    (tmp_path / "gbit.json").write_text(json.dumps(
        {"name": "web3-hub", "tag": "WEB3"}))
    project = load_project(str(tmp_path))
    manager = JobManager()
    proc = _spawn()
    try:
        manager.add(proc, "npm run dev", background=True)
        fragments = build_prompt(str(tmp_path), ShellConfig(), 0,
                                 project=project, jobs=manager)
        text = "".join(part[1] for part in fragments)
        # v1.0.0: project name removed from prompt; only tag shown
        assert "WEB3" in text
        assert "1" in text
    finally:
        manager.terminate_all()


def test_prompt_project_can_hide_git(tmp_path):
    from gbit_shell.config import ShellConfig
    from gbit_shell.prompt import build_prompt

    (tmp_path / "gbit.json").write_text(json.dumps({"show_git": False}))
    project = load_project(str(tmp_path))
    fragments = build_prompt(str(tmp_path), ShellConfig(), 0, project=project)
    classes = [part[0] for part in fragments]
    assert not any("git" in cls for cls in classes)


def test_completer_project_scripts(shell, tmp_path):
    from prompt_toolkit.document import Document

    (tmp_path / "gbit.json").write_text(json.dumps(
        {"scripts": {"deploy": "ghpush", "dev": "npm run dev"}}))
    origin = os.getcwd()
    try:
        shell.executor.run(f"cd {tmp_path}")
        completer = shell._make_completer()
        doc = Document("run de", cursor_position=6)
        results = [c.text for c in completer.get_completions(doc, None)]
        assert "deploy" in results and "dev" in results
    finally:
        os.chdir(origin)


def test_completer_job_specs(shell):
    from prompt_toolkit.document import Document

    proc = _spawn()
    try:
        shell.jobs.add(proc, "npm run dev", background=True)
        completer = shell._make_completer()
        doc = Document("fg %", cursor_position=4)
        results = [c.text for c in completer.get_completions(doc, None)]
        assert "%1" in results
    finally:
        shell.jobs.terminate_all()


def test_completer_project_subcommands(shell):
    from prompt_toolkit.document import Document

    completer = shell._make_completer()
    doc = Document("project in", cursor_position=10)
    results = [c.text for c in completer.get_completions(doc, None)]
    assert "init" in results


def test_completer_help_topics(shell):
    from prompt_toolkit.document import Document

    completer = shell._make_completer()
    doc = Document("help j", cursor_position=6)
    results = [c.text for c in completer.get_completions(doc, None)]
    assert "jobs" in results


# ======================================================================
# Help topics
# ======================================================================

@pytest.mark.parametrize("topic", ["jobs", "project", "config", "github", "keys"])
def test_help_topics_exist(shell, topic, capsys):
    assert BUILTINS["help"]([topic], shell) == 0
    assert capsys.readouterr().out.strip() != ""


def test_help_mentions_jobs_and_project(shell, capsys):
    BUILTINS["help"]([], shell)
    output = capsys.readouterr().out
    assert "jobs" in output
    assert "run <script>" in output
