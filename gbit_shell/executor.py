'''
GBit Shell - Command execution

Hybrid strategy:
  * builtins            -> pure Python (identical on every platform)
  * pipes/globs/&&/>    -> delegated to the system shell
  * everything else     -> spawned directly, with job control attached
'''
import os
import sys
import time
import shlex
import signal
import difflib
import subprocess
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.markup import escape

from .builtins import BUILTINS
from .gitinfo import invalidate_cache
from .jobs import (POSIX, RUNNING, STOPPED, popen_kwargs, wait_allow_stop,
                   give_terminal_to, reclaim_terminal, send, interrupt,
                   kill_tree)
console = Console(highlight=False, soft_wrap=True)

# Commands that change git state -> refresh the prompt cache afterwards
GIT_MUTATORS = ("git", "gh", "ghpush", "ghinit", "ghclone")

SHELL_METACHARS = "|<>`*?$"
SHELL_SEQUENCES = (">>", "2>", "$(")


def split_sequence(line: str) -> List[tuple]:
    """Split a line on top-level &&, || and ; operators.

    Returns [(operator, command), ...] where the first operator is "".
    Operators inside quotes or $( ) are left alone. Splitting these here
    (instead of handing the whole line to the system shell) is what keeps
    `cd build && ls` working with the shell's own builtins.
    """
    parts: List[tuple] = []
    operator = ""
    buffer = ""
    in_single = in_double = False
    depth = 0
    index, length = 0, len(line)

    while index < length:
        char = line[index]
        if char == "\\" and not in_single and index + 1 < length:
            buffer += line[index:index + 2]
            index += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if line.startswith("$(", index):
                depth += 1
            elif char == ")" and depth:
                depth -= 1
            elif depth == 0:
                if line.startswith("&&", index) or line.startswith("||", index):
                    parts.append((operator, buffer.strip()))
                    operator = line[index:index + 2]
                    buffer = ""
                    index += 2
                    continue
                if char == ";":
                    parts.append((operator, buffer.strip()))
                    operator = ";"
                    buffer = ""
                    index += 1
                    continue
        buffer += char
        index += 1

    parts.append((operator, buffer.strip()))
    return [(op, cmd) for op, cmd in parts if cmd or op == ""]


class Executor:
    """Runs command lines for a GBitShell session."""

    def __init__(self, shell):
        self.shell = shell

    # ================================================================
    def run(self, line: str, _depth: int = 0) -> int:
        line = line.strip()
        if not line or line.startswith("#"):
            return 0

        # `cmd1 && cmd2 ; cmd3` -> run each part through the shell itself
        parts = split_sequence(line)
        if len(parts) > 1 and _depth < 8:
            code = 0
            for operator, command in parts:
                if operator == "&&" and code != 0:
                    continue
                if operator == "||" and code == 0:
                    continue
                code = self.run(command, _depth + 1)
            return code

        line = self.shell.resolve_alias(line)
        if len(split_sequence(line)) > 1 and _depth < 8:
            return self.run(line, _depth + 1)

        background = False
        if line.endswith("&") and not line.endswith("&&"):
            background = True
            line = line[:-1].strip()
            if not line:
                return 0

        if self._needs_shell(line):
            return self._run_via_shell(line, background)

        try:
            tokens = shlex.split(line, posix=POSIX)
        except ValueError as exc:
            console.print(f"[red]erro de sintaxe:[/] {escape(str(exc))}")
            return 2
        if not tokens:
            return 0

        head, args = tokens[0], tokens[1:]
        handler = BUILTINS.get(head)

        if handler is not None:
            if background:
                console.print(f"[yellow]{escape(head)} e um comando interno e nao "
                              "pode rodar em segundo plano[/]")
                return 1
            return self._run_builtin(head, handler, args)

        return self._run_external(tokens, background)

    # ----------------------------------------------------------------
    def _run_builtin(self, name: str, handler, args: List[str]) -> int:
        try:
            code = handler(args, self.shell)
        except KeyboardInterrupt:
            console.print("[dim]^C[/]")
            return 130
        except Exception as exc:
            console.print(f"[red]{escape(name)}: {escape(str(exc))}[/]")
            return 1
        if name in GIT_MUTATORS:
            invalidate_cache()
        return code if isinstance(code, int) else 0

    # ----------------------------------------------------------------
    def _needs_shell(self, line: str) -> bool:
        """True when the line uses shell syntax we do not emulate.

        Metacharacters inside quotes do not count.
        """
        in_single = in_double = False
        index, length = 0, len(line)
        while index < length:
            char = line[index]
            if char == "\\\\" and not in_single:
                index += 2
                continue
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double
            elif not in_single and not in_double:
                for sequence in SHELL_SEQUENCES:
                    if line.startswith(sequence, index):
                        return True
                if char in SHELL_METACHARS:
                    return True
            index += 1
        return False

    # ================================================================
    def _run_via_shell(self, line: str, background: bool = False) -> int:
        env = self._child_env()
        kwargs = dict(shell=True, cwd=os.getcwd(), env=env)
        if POSIX:
            kwargs["executable"] = self._system_shell()
        kwargs.update(popen_kwargs(background=background))
        try:
            proc = subprocess.Popen(line, **kwargs)
        except FileNotFoundError:
            console.print("[red]shell do sistema nao encontrado[/]")
            return 127
        except OSError as exc:
            console.print(f"[red]erro de execucao:[/] {escape(str(exc))}")
            return 1
        return self._track(proc, line, background)

    # ----------------------------------------------------------------
    def _run_external(self, tokens: List[str], background: bool = False) -> int:
        program = tokens[0]
        kwargs = dict(cwd=os.getcwd(), env=self._child_env())
        kwargs.update(popen_kwargs(background=background))

        # No Windows, comandos como node/npm sao .cmd/.bat e so funcionam
        # via shell=True. Se o Popen direto falhar com FileNotFoundError,
        # tentamos de novo com shell=True.
        try:
            proc = subprocess.Popen(tokens, **kwargs)
        except FileNotFoundError:
            # Segunda tentativa: via shell do sistema
            # (resolve node.cmd, npm.cmd, etc. no Windows)
            try:
                shell_line = subprocess.list2cmdline(tokens)
                shell_kwargs = dict(
                    shell=True, cwd=os.getcwd(), env=self._child_env(),
                )
                shell_kwargs.update(popen_kwargs(background=background))
                proc = subprocess.Popen(shell_line, **shell_kwargs)
            except FileNotFoundError:
                console.print(f"[red]comando nao encontrado:[/] {escape(program)}")
                hint = self._suggest(program)
                if hint:
                    console.print(f"[dim]quis dizer[/] [cyan]{escape(hint)}[/][dim]?[/]")
                return 127
            except OSError as exc2:
                console.print(f"[red]erro ao executar {escape(program)}:[/] "
                              f"{escape(str(exc2))}")
                return 1
        except PermissionError:
            console.print(f"[red]permissao negada:[/] {escape(program)}")
            return 126
        except OSError as exc:
            console.print(f"[red]erro ao executar {escape(program)}:[/] "
                          f"{escape(str(exc))}")
            return 1

        code = self._track(proc, " ".join(tokens), background)
        if program in GIT_MUTATORS:
            invalidate_cache()
        return code

    # ================================================================
    def _track(self, proc: subprocess.Popen, command: str,
               background: bool) -> int:
        """Register a background job, or wait for a foreground one."""
        if background:
            job = self.shell.jobs.add(proc, command, background=True)
            console.print(f"[dim][{job.id}] {job.pid}[/]")
            return 0
        return self._wait_foreground(proc, command)

    # ----------------------------------------------------------------
    def _wait_foreground(self, proc: subprocess.Popen, command: str,
                         job=None) -> int:
        """Wait for a child while keeping Ctrl+C and Ctrl+Z usable."""
        previous_owner = give_terminal_to(proc) if POSIX else None
        restore_tstp = self._forward_tstp(proc, handed_over=previous_owner is not None)
        interrupted = False
        attempts = 0
        try:
            while True:
                try:
                    kind, value = wait_allow_stop(proc)
                except KeyboardInterrupt:
                    interrupted = True
                    attempts += 1
                    if attempts == 1:
                        console.print("[dim]^C[/]")
                        interrupt(proc)
                    else:
                        console.print("[yellow]^C encerrando a forca...[/]")
                        kill_tree(proc)
                    # Give the child a moment to die before waiting again;
                    # if it ignores the interrupt, the next Ctrl+C kills it.
                    for _ in range(20):
                        if proc.poll() is not None:
                            break
                        time.sleep(0.05)
                    continue

                if kind == "stopped":
                    return self._register_stopped(proc, command, job)

                if job is not None:
                    job.mark_finished(value)
                    job.reported = True
                if interrupted:
                    return 130
                return value

        finally:
            reclaim_terminal(previous_owner)
            if restore_tstp is not None:
                restore_tstp()

    # ----------------------------------------------------------------
    def _forward_tstp(self, proc, handed_over: bool):
        """When the child does not own the terminal, relay Ctrl+Z to it.

        Returns a callable that restores the previous handler, or None.
        """
        if not POSIX or handed_over:
            return None
        try:
            previous = signal.getsignal(signal.SIGTSTP)

            def relay(signum, frame):
                send(proc, signal.SIGTSTP)

            signal.signal(signal.SIGTSTP, relay)
        except (OSError, ValueError):
            return None

        def restore():
            try:
                signal.signal(signal.SIGTSTP, previous)
            except (OSError, ValueError):
                pass

        return restore

    # ----------------------------------------------------------------
    def _register_stopped(self, proc: subprocess.Popen, command: str,
                          job=None) -> int:
        """A foreground child got Ctrl+Z: keep it as a suspended job."""
        if job is None:
            job = self.shell.jobs.add(proc, command, background=True,
                                      state=STOPPED)
        self.shell.jobs.mark_stopped(job)
        console.print(f"\n[yellow][{job.id}]+  suspenso[/]  {escape(job.command)}")
        console.print("[dim]retome com[/] [cyan]fg[/] [dim]ou continue em "
                      "segundo plano com[/] [cyan]bg[/]")
        return 148

    # ----------------------------------------------------------------
    def resume_foreground(self, job) -> int:
        """`fg`: continue a job and wait for it again."""
        console.print(f"[dim]{escape(job.command)}[/]")
        if not self.shell.jobs.resume(job, background=False):
            console.print("[red]nao foi possivel retomar o job[/]")
            return 1
        return self._wait_foreground(job.proc, job.command, job=job)

    # ================================================================
    def _child_env(self) -> dict:
        env = dict(os.environ)
        env.update(self.shell.config.env)
        project = self.shell.project
        if project.exists:
            env.update(project.env)
            env["GBIT_PROJECT"] = project.name
            if project.root is not None:
                env["GBIT_PROJECT_ROOT"] = str(project.root)
        env["GBIT_SHELL"] = self.shell.version
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("GIT_PAGER", "cat")
        return env

    # ----------------------------------------------------------------
    def _system_shell(self) -> Optional[str]:
        for candidate in (os.environ.get("SHELL"), "/bin/bash", "/bin/sh"):
            if candidate and os.path.exists(candidate):
                return candidate
        return None

    # ----------------------------------------------------------------
    def _suggest(self, program: str) -> Optional[str]:
        pool = set(BUILTINS) | set(self.shell.all_aliases())
        if self.shell.project.exists:
            pool |= set(self.shell.project.scripts)
        matches = difflib.get_close_matches(program, sorted(pool), n=1, cutoff=0.7)
        return matches[0] if matches else None
