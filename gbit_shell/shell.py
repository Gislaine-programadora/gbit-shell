'''
GBit Shell - Interactive session
Glues together configuration, per-project gbit.json, job control,
completion, prompt rendering and the executor.
'''
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from rich import box

from . import __version__
from .config import ShellConfig, HISTORY_FILE, GBIT_DIR
from .builtins import BUILTINS
from .executor import Executor
from .jobs import (JobManager, POSIX, RUNNING, STOPPED, DONE, FAILED,
                   prepare_shell_signals, claim_terminal)
from .project import load_project, PROJECT_FILE

console = Console(highlight=False, soft_wrap=True)

MAX_ALIAS_DEPTH = 8


class GBitShell:
    """The GBit Shell session."""

    def __init__(self, no_banner: bool = False, interactive: bool = True):
        self.version = __version__
        self.config = ShellConfig()
        self.builtins = BUILTINS
        self.executor = Executor(self)
        self.jobs = JobManager()
        self.running = True
        self.last_exit = 0
        self.previous_dir: Optional[str] = None
        self.no_banner = no_banner
        self.interactive = interactive
        self.session = None
        self._exit_warned = False

        # gbit.json state
        self.project = load_project(os.getcwd())
        self._project_dir = str(Path(os.getcwd()).resolve())
        self._project_env: List[str] = []
        self._apply_project()

        GBIT_DIR.mkdir(parents=True, exist_ok=True)
        self.first_run = self.config.save_rc_if_missing()

        if interactive:
            self.session = self._build_session()

    # ================================================================
    # Per-project configuration
    # ================================================================
    def refresh_project(self, announce: bool = True) -> None:
        """Reload gbit.json if the current directory moved to another tree."""
        here = str(Path(os.getcwd()).resolve())
        if here == self._project_dir:
            return
        self._project_dir = here

        previous_root = self.project.root if self.project.exists else None
        self.project = load_project(here)
        new_root = self.project.root if self.project.exists else None

        if new_root == previous_root:
            return

        self._clear_project_env()
        self._apply_project()

        if not announce:
            return
        if self.project.exists and self.project.error:
            console.print(f"[yellow]{PROJECT_FILE}: {escape(self.project.error)}[/]")
        elif self.project.exists:
            console.print(f"[dim]projeto[/] [bold cyan]{escape(self.project.name)}[/] "
                          f"[dim]· {PROJECT_FILE} carregado[/]")

    # ----------------------------------------------------------------
    def _apply_project(self) -> None:
        """Export the project's env vars and apply its theme."""
        if not self.project.exists:
            return
        for key, value in self.project.env.items():
            os.environ[key] = value
            self._project_env.append(key)
        if self.project.theme:
            self.set_theme(self.project.theme, persist=False)

    def _clear_project_env(self) -> None:
        for key in self._project_env:
            os.environ.pop(key, None)
        self._project_env = []

    # ----------------------------------------------------------------
    def all_aliases(self) -> Dict[str, str]:
        """Global aliases merged with the project's (project wins)."""
        merged = dict(self.config.aliases)
        if self.project.exists:
            merged.update(self.project.aliases)
        return merged

    def resolve_alias(self, line: str) -> str:
        """Expand aliases repeatedly, stopping at loops."""
        aliases = self.all_aliases()
        seen = set()
        current = line.strip()
        for _ in range(MAX_ALIAS_DEPTH):
            parts = current.split(None, 1)
            if not parts:
                return current
            head = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            if head not in aliases or head in seen:
                return current
            seen.add(head)
            expansion = aliases[head]
            current = f"{expansion} {rest}".strip() if rest else expansion
        return current

    # ================================================================
    # Jobs
    # ================================================================
    def reap_jobs(self) -> None:
        """Report jobs that finished since the last prompt."""
        for job in self.jobs.poll():
            if job.reported:
                continue
            job.reported = True
            status = ("[green]concluido[/]" if job.state == DONE
                      else f"[red]falhou (codigo {job.exit_code})[/]")
            console.print(f"[dim][{job.id}][/]  {status}  {escape(job.command)}")
        self.jobs.prune()

    def confirm_exit(self) -> bool:
        """Ask once before leaving with suspended jobs behind."""
        stopped = self.jobs.stopped()
        if stopped and not self._exit_warned:
            self._exit_warned = True
            console.print(f"[yellow]ha {len(stopped)} job(s) suspenso(s).[/] "
                          "[dim]use[/] [bold]jobs[/] [dim]para ver ou repita[/] "
                          "[bold]exit[/] [dim]para sair de qualquer forma[/]")
            return False
        self._exit_warned = False
        return True

    def shutdown_jobs(self) -> None:
        live = [job for job in self.jobs.active()]
        if live and self.interactive:
            console.print(f"[dim]encerrando {len(live)} job(s)...[/]")
        self.jobs.terminate_all()

    # ================================================================
    # prompt_toolkit plumbing
    # ================================================================
    def _make_completer(self):
        from .completer import GBitCompleter
        return GBitCompleter(self.config, self.builtins.keys(), shell=self)

    def _build_session(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.enums import EditingMode
        from prompt_toolkit.output.color_depth import ColorDepth

        from .styles import get_style

        bindings = KeyBindings()

        @bindings.add("c-l")
        def _clear(event):
            event.app.renderer.clear()

        @bindings.add("c-d")
        def _eof(event):
            if event.current_buffer.text:
                event.current_buffer.delete()
            else:
                self.running = False
                event.app.exit(result="")

        @bindings.add("c-z")
        def _jobs_shortcut(event):
            # Nothing is running at the prompt, so show the job list
            # instead of suspending the shell itself.
            if not event.current_buffer.text:
                event.current_buffer.text = "jobs"
                event.current_buffer.validate_and_handle()

        return PromptSession(
            history=FileHistory(str(HISTORY_FILE)),
            auto_suggest=(AutoSuggestFromHistory()
                          if self.config.get("suggestions", True) else None),
            completer=self._make_completer(),
            complete_while_typing=False,
            style=get_style(self.config.get("theme", "gbit")),
            key_bindings=bindings,
            editing_mode=EditingMode.EMACS,
            enable_history_search=True,
            mouse_support=False,
            color_depth=ColorDepth.TRUE_COLOR,
            reserve_space_for_menu=6,
        )

    # ----------------------------------------------------------------
    def set_theme(self, name: str, persist: bool = True) -> None:
        self.config.set("theme", name)
        if self.session is not None:
            from .styles import get_style
            self.session.style = get_style(name)

    def reload_config(self) -> None:
        self._clear_project_env()
        self.config = ShellConfig()
        self.project = load_project(os.getcwd())
        self._apply_project()
        if self.session is not None:
            from .styles import get_style
            self.session.completer = self._make_completer()
            self.session.style = get_style(self.config.get("theme", "gbit"))

    def get_history(self, limit: int = 30) -> List[str]:
        if self.session is None:
            return []
        try:
            return list(self.session.history.get_strings())[-limit:]
        except Exception:
            return []

    # ================================================================
    # Terminal title
    # ================================================================
    def set_terminal_title(self, title: str) -> None:
        """Name the tab immediately (OSC 0/2).

        Without this the VS Code tab shows the process name ("python")
        until something else renames it seconds later.
        """
        if not self.interactive:
            return
        try:
            if not sys.stdout.isatty():
                return
            sys.stdout.write(f"\033]0;{title}\007")
            sys.stdout.flush()
        except Exception:
            pass

    # ================================================================
    # Banner (exibido apenas no CLI `gbit`, nunca no terminal VS Code)
    # ================================================================
    def print_banner(self, force: bool = False) -> None:

        if not force:
            if self.no_banner:
                return
            if os.environ.get("GBIT_VSCODE"):
                return

        logo = [
            "  ____  ____  ___  _____ ",
            " / ___|| __ )|_ \\|_   _|",
            "| |  _ |  _ \\ | |  | |  ",
            "| |_| || |_) || |  | |  ",
            " \\____||____/|___| |_|  ",
        ]

        console.print()
        for i, line in enumerate(logo):
            text = Text(line, style="bold cyan")
            if i == 2:
                text.append("  TERMINAL-SHELL", style="bold white")
                text.append(f"  [v{self.version}]", style="bold cyan")
            console.print(text)

        console.print()
        tagline = Text()
        tagline.append("  Shell ", style="dim white")
        tagline.append(f"v{self.version}", style="dim white")
        tagline.append("  ·  ", style="dim white")
        tagline.append("digite ", style="dim white")
        tagline.append("help", style="bold cyan")
        tagline.append(" para começar  ·  ", style="dim white")
        tagline.append("ghpush", style="bold cyan")
        tagline.append(" publica no GitHub", style="dim white")
        console.print(tagline)

        if self.project.exists:
            extra = f" · [dim]{len(self.project.scripts)} script(s)[/]" \
                if self.project.scripts else ""
            console.print(f"  [dim]projeto[/] [bold]⬢ {escape(self.project.name)}[/]"
                          f"{extra}")
        console.print()

        if self.first_run:
            console.print(Panel(
                "Criei [bold cyan]~/.gbitrc[/] com a configuracao inicial.\n"
                "Edite para adicionar aliases e escolher o tema.\n"
                "Veja [bold]help config[/] para a sintaxe.",
                title="[bold green]Primeira execucao[/]",
                border_style="green", box=box.ROUNDED))
            console.print()

    # ----------------------------------------------------------------
    def run_startup_commands(self) -> None:
        for command in self.config.startup_commands:
            self.executor.run(command)

    # ================================================================
    # Main loop
    # ================================================================
    def run(self) -> int:
        from prompt_toolkit.formatted_text import FormattedText
        from .prompt import build_prompt

        self.set_terminal_title("GBit Shell")
        self.print_banner()

        if POSIX:
            # Ctrl+Z must suspend the running program, never the shell
            prepare_shell_signals()
            claim_terminal()
        self.run_startup_commands()

        while self.running:
            try:
                self.reap_jobs()
                self.refresh_project()
                fragments = build_prompt(os.getcwd(), self.config, self.last_exit,
                                         project=self.project, jobs=self.jobs)
                line = self.session.prompt(FormattedText(fragments))
            except KeyboardInterrupt:
                continue
            except EOFError:
                break

            if not line or not line.strip():
                continue
            if line.strip() != "exit":
                self._exit_warned = False
            self.last_exit = self.executor.run(line)

        self.shutdown_jobs()
        console.print("[dim]tchau — GBit Shell[/]")
        return self.last_exit

    # ----------------------------------------------------------------
    def run_single(self, command: str) -> int:
        """Non-interactive mode: gbit -c \"...\""""
        self.run_startup_commands()
        code = self.executor.run(command)
        self.jobs.terminate_all()
        return code
