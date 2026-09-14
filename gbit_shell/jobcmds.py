'''
GBit Shell - Builtins for job control and per-project config

  jobs / fg / bg / kill / wait   -> control child processes
  project / run                  -> gbit.json inspection and scripts
'''
import os
import json
import signal
import time
from pathlib import Path
from typing import List

from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.panel import Panel
from rich import box

from .jobs import POSIX, RUNNING, STOPPED
from .project import (PROJECT_FILE, default_template, detect_project_kind,
                      write_project_file)

console = Console(highlight=False, soft_wrap=True)

STATE_LABEL = {
    "running": "[green]rodando[/]",
    "stopped": "[yellow]suspenso[/]",
    "done": "[dim]concluido[/]",
    "failed": "[red]falhou[/]",
}


def _fmt_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


# ======================================================================
# jobs
# ======================================================================

def cmd_jobs(args: List[str], shell) -> int:
    """List background and suspended jobs."""
    shell.jobs.poll()
    show_all = any(a in ("-a", "--all") for a in args)
    pids_only = any(a in ("-p", "--pid") for a in args)

    entries = shell.jobs.all() if show_all else shell.jobs.active()
    if not entries:
        console.print("[dim]nenhum job ativo[/]")
        return 0

    if pids_only:
        for job in entries:
            console.print(str(job.pid))
        return 0

    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("#", style="cyan bold", justify="right")
    table.add_column("", width=1)
    table.add_column("PID", style="dim", justify="right")
    table.add_column("Estado")
    table.add_column("Tempo", style="dim", justify="right")
    table.add_column("Comando")

    for job in entries:
        marker = "+" if job.id == shell.jobs.current else (
            "-" if job.id == shell.jobs.previous else " ")
        table.add_row(str(job.id), marker, str(job.pid),
                      STATE_LABEL.get(job.state, job.state),
                      _fmt_elapsed(job.elapsed), escape(job.command))
    console.print(table)
    if not POSIX:
        console.print("[dim]no Windows so jobs em segundo plano (&) sao "
                      "suportados; suspender com Ctrl+Z nao existe[/]")
    return 0


# ======================================================================
# fg / bg
# ======================================================================

def _resolve(args: List[str], shell):
    spec = args[0] if args else None
    job = shell.jobs.get(spec)
    if job is None:
        if spec:
            console.print(f"[red]job nao encontrado:[/] {escape(spec)}")
        else:
            console.print("[dim]nenhum job ativo[/]")
        return None
    if not job.is_active():
        console.print(f"[yellow]job {job.id} ja terminou[/]")
        return None
    return job


def cmd_fg(args: List[str], shell) -> int:
    """Bring a job back to the foreground."""
    if not POSIX:
        console.print("[yellow]fg nao esta disponivel no Windows[/]")
        return 1
    shell.jobs.poll()
    job = _resolve(args, shell)
    if job is None:
        return 1
    return shell.executor.resume_foreground(job)


def cmd_bg(args: List[str], shell) -> int:
    """Resume a suspended job, keeping it in the background."""
    if not POSIX:
        console.print("[yellow]bg nao esta disponivel no Windows[/]")
        return 1
    shell.jobs.poll()
    job = _resolve(args, shell)
    if job is None:
        return 1
    if job.state == RUNNING and job.background:
        console.print(f"[dim]job {job.id} ja roda em segundo plano[/]")
        return 0
    if not shell.jobs.resume(job, background=True):
        console.print("[red]nao foi possivel continuar o processo[/]")
        return 1
    console.print(f"[dim][{job.id}]+ {escape(job.command)} &[/]")
    return 0


# ======================================================================
# kill / wait
# ======================================================================

SIGNAL_ALIASES = {
    "TERM": "SIGTERM", "KILL": "SIGKILL", "INT": "SIGINT",
    "HUP": "SIGHUP", "STOP": "SIGSTOP", "CONT": "SIGCONT",
    "QUIT": "SIGQUIT", "USR1": "SIGUSR1", "USR2": "SIGUSR2",
}


def _parse_signal(token: str):
    name = token.lstrip("-").upper()
    if name.isdigit():
        try:
            return signal.Signals(int(name))
        except ValueError:
            return None
    name = SIGNAL_ALIASES.get(name, name)
    if not name.startswith("SIG"):
        name = "SIG" + name
    return getattr(signal, name, None)


def cmd_kill(args: List[str], shell) -> int:
    """kill [-SIG] %job | pid ..."""
    if not args:
        console.print("[yellow]uso: kill [-TERM|-KILL|-9] %<job> | <pid>[/]")
        return 1

    if args[0] in ("-l", "--list"):
        names = sorted(s.name for s in signal.Signals)
        console.print("  ".join(names))
        return 0

    sig = signal.SIGTERM
    targets = list(args)
    if targets[0].startswith("-"):
        parsed = _parse_signal(targets[0])
        if parsed is None:
            console.print(f"[red]sinal invalido:[/] {escape(targets[0])}")
            return 1
        sig = parsed
        targets = targets[1:]

    if not targets:
        console.print("[yellow]informe um job (%1) ou pid[/]")
        return 1

    shell.jobs.poll()
    failures = 0
    for target in targets:
        is_job_spec = target.startswith("%") or not target.isdigit()
        if is_job_spec:
            job = shell.jobs.get(target)
            if job is None:
                console.print(f"[red]job nao encontrado:[/] {escape(target)}")
                failures += 1
                continue
            if job.state == STOPPED and POSIX:
                shell.jobs.signal_job(job, signal.SIGCONT)
            if shell.jobs.signal_job(job, sig):
                console.print(f"[dim]{sig.name} -> job {job.id} (pid {job.pid})[/]")
            else:
                console.print(f"[red]falhou ao sinalizar job {job.id}[/]")
                failures += 1
            continue

        if not target.isdigit():
            console.print(f"[red]pid invalido:[/] {escape(target)}")
            failures += 1
            continue
        try:
            os.kill(int(target), sig)
            console.print(f"[dim]{sig.name} -> pid {target}[/]")
        except ProcessLookupError:
            console.print(f"[red]processo nao encontrado:[/] {target}")
            failures += 1
        except PermissionError:
            console.print(f"[red]permissao negada para o pid {target}[/]")
            failures += 1
        except OSError as exc:
            console.print(f"[red]erro:[/] {escape(str(exc))}")
            failures += 1
    return 1 if failures else 0


def cmd_wait(args: List[str], shell) -> int:
    """Wait for background jobs to finish."""
    shell.jobs.poll()
    if args:
        job = shell.jobs.get(args[0])
        if job is None:
            console.print(f"[red]job nao encontrado:[/] {escape(args[0])}")
            return 1
        pending = [job]
    else:
        pending = [j for j in shell.jobs.active() if j.state == RUNNING]

    if not pending:
        console.print("[dim]nada para aguardar[/]")
        return 0

    stopped = [j for j in pending if j.state == STOPPED]
    if stopped:
        console.print(f"[yellow]job {stopped[0].id} esta suspenso; "
                      "use bg antes de aguardar[/]")
        return 1

    code = 0
    try:
        for job in pending:
            job.proc.wait()
            code = job.proc.returncode or 0
    except KeyboardInterrupt:
        console.print("[dim]^C[/]")
        return 130
    shell.reap_jobs()
    return code


# ======================================================================
# disown
# ======================================================================

def cmd_disown(args: List[str], shell) -> int:
    """Remove jobs from the table without killing them."""
    shell.jobs.poll()
    if any(a in ("-a", "--all") for a in args):
        targets = list(shell.jobs.active())
    else:
        job = shell.jobs.get(args[0] if args else None)
        if job is None:
            if args:
                console.print(f"[red]job nao encontrado:[/] {escape(args[0])}")
            else:
                console.print("[dim]nenhum job ativo[/]")
            return 1
        targets = [job]

    if not targets:
        console.print("[dim]nenhum job ativo[/]")
        return 0

    for job in targets:
        if shell.jobs.forget(job):
            console.print(f"[dim]job {job.id} removido da lista "
                          f"(pid {job.pid} continua rodando)[/]")
    return 0


# ======================================================================
# project / run
# ======================================================================

def cmd_project(args: List[str], shell) -> int:
    """project [init|info|scripts|path|reload]"""
    action = args[0] if args else "info"

    if action == "init":
        force = any(a in ("-f", "--force") for a in args[1:])
        cwd = os.getcwd()
        name = Path(cwd).name
        detected = detect_project_kind(cwd)
        data = default_template(name, detected)
        written, message = write_project_file(cwd, data, force=force)
        if not written:
            console.print(f"[yellow]{escape(message)}[/]")
            return 1
        console.print(f"[green]{escape(message)}[/] [dim]em {escape(name)}[/]")
        shell._project_dir = None
        shell.refresh_project(announce=False)
        console.print("[dim]edite gbit.json para adicionar aliases, env e scripts[/]")
        return 0

    if action == "reload":
        shell._project_dir = None
        shell.refresh_project(announce=False)
        if shell.project.exists:
            console.print(f"[green]{PROJECT_FILE} recarregado[/]")
        else:
            console.print(f"[dim]nenhum {PROJECT_FILE} encontrado[/]")
        return 0

    project = shell.project
    if not project.exists:
        console.print(Panel(
            f"Nenhum [bold cyan]{PROJECT_FILE}[/] encontrado desta pasta para cima.\n"
            "Crie um com [bold]project init[/].",
            border_style="yellow", box=box.ROUNDED))
        return 1

    if action == "path":
        console.print(str(project.path))
        return 0

    if action == "scripts":
        scripts = project.scripts
        if not scripts:
            console.print("[dim]nenhum script definido[/]")
            return 0
        table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        table.add_column("Script", style="cyan bold")
        table.add_column("Comandos")
        for name in sorted(scripts):
            table.add_row(escape(name), escape(" && ".join(scripts[name])))
        console.print(table)
        return 0

    # default: info
    lines = [f"[bold]{escape(project.name or '-')}[/]"]
    if project.data.get("description"):
        lines.append(f"[dim]{escape(str(project.data['description']))}[/]")
    lines.append(f"[dim]arquivo:[/] {escape(str(project.path))}")
    if project.tag:
        lines.append(f"[dim]selo:[/] {escape(project.tag)}")
    if project.theme:
        lines.append(f"[dim]tema:[/] {escape(project.theme)}")
    console.print(Panel("\n".join(lines), title="[bold]Projeto[/]",
                        border_style="cyan", box=box.ROUNDED))

    if project.aliases:
        table = Table(box=box.SIMPLE, show_header=False,
                      title="[bold]Aliases do projeto[/]", title_justify="left")
        table.add_column("", style="cyan bold")
        table.add_column("")
        for name in sorted(project.aliases):
            table.add_row(escape(name), escape(project.aliases[name]))
        console.print(table)

    if project.env:
        table = Table(box=box.SIMPLE, show_header=False,
                      title="[bold]Variaveis[/]", title_justify="left")
        table.add_column("", style="green")
        table.add_column("")
        for key in sorted(project.env):
            value = project.env[key]
            hidden = any(word in key.upper()
                         for word in ("TOKEN", "SECRET", "KEY", "PASS"))
            table.add_row(escape(key), "[dim]***[/]" if hidden else escape(value))
        console.print(table)

    if project.scripts:
        cmd_project(["scripts"], shell)

    return 0


def cmd_run(args: List[str], shell) -> int:
    """Run a script declared in gbit.json (never runs automatically)."""
    project = shell.project
    scripts = project.scripts

    if not args:
        if not scripts:
            console.print(f"[dim]nenhum script em {PROJECT_FILE}[/]")
            return 1
        console.print("[dim]scripts disponiveis:[/] " +
                      ", ".join(f"[cyan]{escape(s)}[/]" for s in sorted(scripts)))
        return 0

    name = args[0]
    if name not in scripts:
        console.print(f"[red]script nao encontrado:[/] {escape(name)}")
        if scripts:
            console.print("[dim]disponiveis:[/] " + ", ".join(sorted(scripts)))
        return 1

    extra = " ".join(args[1:])
    steps = scripts[name]
    root = str(project.root) if project.root else os.getcwd()
    origin = os.getcwd()

    # Scripts always run from the project root, like npm does.
    if root != origin:
        try:
            os.chdir(root)
        except OSError as exc:
            console.print(f"[red]nao consegui entrar em {escape(root)}:[/] "
                          f"{escape(str(exc))}")
            return 1

    code = 0
    try:
        for index, step in enumerate(steps, start=1):
            command = f"{step} {extra}".strip() if (extra and index == len(steps)) else step
            if len(steps) > 1:
                console.print(f"[dim]({index}/{len(steps)})[/] [cyan]{escape(command)}[/]")
            else:
                console.print(f"[dim]$ {escape(command)}[/]")
            code = shell.executor.run(command)
            if code != 0:
                console.print(f"[red]script '{escape(name)}' parou no passo "
                              f"{index} (codigo {code})[/]")
                break
    finally:
        if root != origin:
            try:
                os.chdir(origin)
            except OSError:
                pass
    return code
