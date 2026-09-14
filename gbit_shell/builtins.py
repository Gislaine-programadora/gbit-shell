'''
GBit Shell - Built-in commands
Implemented natively in Python so they work identically on Windows,
macOS and Linux without depending on coreutils.

Every handler signature: fn(args: List[str], shell) -> int (exit code)
'''
import os
import sys
import shutil
import socket
import platform
import subprocess
import webbrowser
from pathlib import Path
from typing import List, Dict, Callable, Optional

from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.panel import Panel
from rich import box

from . import __version__
from . import github as gh
from .gitinfo import get_git_info, invalidate_cache
from .styles import theme_names
from .jobcmds import (cmd_jobs, cmd_fg, cmd_bg, cmd_kill, cmd_wait,
                      cmd_disown, cmd_project, cmd_run)
from .project import PROJECT_FILE
from .vscode import cmd_vscode

console = Console(highlight=False, soft_wrap=True)


# ======================================================================
# Navigation / filesystem
# ======================================================================

def cmd_cd(args: List[str], shell) -> int:
    if not args:
        target = str(Path.home())
    elif args[0] == "-":
        target = shell.previous_dir or os.getcwd()
    else:
        target = os.path.expanduser(os.path.expandvars(args[0]))
    try:
        previous = os.getcwd()
        os.chdir(target)
        shell.previous_dir = previous
        # A new directory may belong to another gbit.json
        shell.refresh_project()
        return 0
    except FileNotFoundError:
        console.print(f"[red]cd: diretorio nao encontrado:[/] {target}")
    except NotADirectoryError:
        console.print(f"[red]cd: nao e um diretorio:[/] {target}")
    except PermissionError:
        console.print(f"[red]cd: permissao negada:[/] {target}")
    return 1


def cmd_pwd(args: List[str], shell) -> int:
    sys.stdout.write(os.getcwd() + "\n")
    return 0


def cmd_ls(args: List[str], shell) -> int:
    show_all = any(a.startswith("-") and "a" in a for a in args)
    long_form = any(a.startswith("-") and "l" in a for a in args)
    paths = [a for a in args if not a.startswith("-")] or ["."]

    for path in paths:
        target = Path(os.path.expanduser(path))
        if not target.exists():
            console.print(f"[red]ls: nao encontrado:[/] {path}")
            return 1
        if target.is_file():
            sys.stdout.write(target.name + "\n")
            continue
        try:
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            console.print(f"[red]ls: permissao negada:[/] {path}")
            return 1
        if not show_all:
            entries = [e for e in entries if not e.name.startswith(".")]

        if long_form:
            table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
            table.add_column("Tipo", width=4)
            table.add_column("Tamanho", justify="right", width=10)
            table.add_column("Nome")
            for entry in entries:
                try:
                    stat = entry.stat()
                    size = "-" if entry.is_dir() else _human_size(stat.st_size)
                except OSError:
                    size = "?"
                kind = "dir" if entry.is_dir() else "file"
                color = "cyan bold" if entry.is_dir() else "white"
                table.add_row(kind, size, f"[{color}]{escape(entry.name)}[/]")
            console.print(table)
        else:
            rendered = []
            for entry in entries:
                safe = escape(entry.name)
                if entry.is_dir():
                    rendered.append(f"[cyan bold]{safe}/[/]")
                elif os.access(entry, os.X_OK) and not entry.is_dir():
                    rendered.append(f"[green]{safe}[/]")
                else:
                    rendered.append(safe)
            if rendered:
                console.print("   ".join(rendered))
    return 0


def cmd_cat(args: List[str], shell) -> int:
    if not args:
        console.print("[yellow]uso: cat <arquivo>[/]")
        return 1
    code = 0
    for name in args:
        path = Path(os.path.expanduser(name))
        try:
            # Write raw so file contents are never parsed as Rich markup
            sys.stdout.write(path.read_text(encoding="utf-8", errors="replace"))
            sys.stdout.flush()
        except FileNotFoundError:
            console.print(f"[red]cat: nao encontrado:[/] {name}")
            code = 1
        except IsADirectoryError:
            console.print(f"[red]cat: e um diretorio:[/] {name}")
            code = 1
        except PermissionError:
            console.print(f"[red]cat: permissao negada:[/] {name}")
            code = 1
    return code


def cmd_mkdir(args: List[str], shell) -> int:
    if not args:
        console.print("[yellow]uso: mkdir <diretorio>[/]")
        return 1
    targets = [a for a in args if not a.startswith("-")]
    for name in targets:
        try:
            Path(os.path.expanduser(name)).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            console.print(f"[red]mkdir: {exc}[/]")
            return 1
    return 0


def cmd_touch(args: List[str], shell) -> int:
    if not args:
        console.print("[yellow]uso: touch <arquivo>[/]")
        return 1
    for name in args:
        try:
            Path(os.path.expanduser(name)).touch()
        except OSError as exc:
            console.print(f"[red]touch: {exc}[/]")
            return 1
    return 0


def cmd_rm(args: List[str], shell) -> int:
    recursive = any(a.startswith("-") and ("r" in a or "R" in a) for a in args)
    force = any(a.startswith("-") and "f" in a for a in args)
    targets = [a for a in args if not a.startswith("-")]
    if not targets:
        console.print("[yellow]uso: rm [-rf] <caminho>[/]")
        return 1
    for name in targets:
        path = Path(os.path.expanduser(name))
        if not path.exists():
            if not force:
                console.print(f"[red]rm: nao encontrado:[/] {name}")
                return 1
            continue
        try:
            if path.is_dir():
                if not recursive:
                    console.print(f"[red]rm: '{name}' e um diretorio (use -r)[/]")
                    return 1
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            console.print(f"[red]rm: {exc}[/]")
            return 1
    return 0


def cmd_cp(args: List[str], shell) -> int:
    recursive = any(a.startswith("-") and ("r" in a or "R" in a) for a in args)
    targets = [a for a in args if not a.startswith("-")]
    if len(targets) < 2:
        console.print("[yellow]uso: cp [-r] <origem> <destino>[/]")
        return 1
    src, dest = Path(os.path.expanduser(targets[0])), Path(os.path.expanduser(targets[-1]))
    try:
        if src.is_dir():
            if not recursive:
                console.print("[red]cp: origem e um diretorio (use -r)[/]")
                return 1
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)
    except OSError as exc:
        console.print(f"[red]cp: {exc}[/]")
        return 1
    return 0


def cmd_mv(args: List[str], shell) -> int:
    targets = [a for a in args if not a.startswith("-")]
    if len(targets) < 2:
        console.print("[yellow]uso: mv <origem> <destino>[/]")
        return 1
    try:
        shutil.move(os.path.expanduser(targets[0]), os.path.expanduser(targets[-1]))
    except OSError as exc:
        console.print(f"[red]mv: {exc}[/]")
        return 1
    return 0


def cmd_which(args: List[str], shell) -> int:
    if not args:
        console.print("[yellow]uso: which <comando>[/]")
        return 1
    code = 0
    for name in args:
        if name in shell.builtins:
            console.print(f"[cyan]{name}[/]: builtin do GBit Shell")
            continue
        if name in shell.config.aliases:
            console.print(f"[violet]{name}[/]: alias -> {shell.config.aliases[name]}")
            continue
        found = shutil.which(name)
        if found:
            sys.stdout.write(found + "\n")
        else:
            console.print(f"[red]{name} nao encontrado[/]")
            code = 1
    return code


def cmd_clear(args: List[str], shell) -> int:
    console.clear()
    return 0


# ======================================================================
# Shell environment
# ======================================================================

def cmd_alias(args: List[str], shell) -> int:
    if not args:
        merged = shell.all_aliases()
        project_names = set(shell.project.aliases)
        table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        table.add_column("Alias", style="cyan bold")
        table.add_column("Comando")
        table.add_column("Origem", style="dim")
        for name in sorted(merged):
            origin = "gbit.json" if name in project_names else "~/.gbitrc"
            table.add_row(escape(name), escape(merged[name]), origin)
        console.print(table)
        return 0
    joined = " ".join(args)
    if "=" not in joined:
        name = joined.strip()
        merged = shell.all_aliases()
        if name in merged:
            console.print(f"{name} -> {merged[name]}")
            return 0
        console.print(f"[red]alias nao definido: {name}[/]")
        return 1
    name, value = joined.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    shell.config.aliases[name.strip()] = value
    console.print(f"[green]alias definido:[/] {name.strip()} -> {value}")
    return 0


def cmd_unalias(args: List[str], shell) -> int:
    if not args:
        console.print("[yellow]uso: unalias <nome>[/]")
        return 1
    for name in args:
        if shell.config.aliases.pop(name, None) is None:
            console.print(f"[red]alias nao encontrado: {name}[/]")
            return 1
    return 0


def cmd_export(args: List[str], shell) -> int:
    if not args:
        return cmd_env(args, shell)
    joined = " ".join(args)
    if "=" not in joined:
        console.print("[yellow]uso: export CHAVE=valor[/]")
        return 1
    key, value = joined.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    os.environ[key.strip()] = os.path.expandvars(value)
    console.print(f"[green]{key.strip()}[/]={os.environ[key.strip()]}")
    return 0


def cmd_unset(args: List[str], shell) -> int:
    for name in args:
        os.environ.pop(name, None)
    return 0


def cmd_env(args: List[str], shell) -> int:
    filt = args[0].lower() if args else None
    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("Variavel", style="cyan")
    table.add_column("Valor", overflow="fold")
    for key in sorted(os.environ):
        if filt and filt not in key.lower():
            continue
        value = os.environ[key]
        if any(s in key.upper() for s in ("TOKEN", "SECRET", "PASSWORD", "KEY", "PASS")):
            value = "•" * 8 + " (oculto)"
        table.add_row(escape(key), escape(value))
    console.print(table)
    return 0


def cmd_history(args: List[str], shell) -> int:
    limit = 30
    if args and args[0].isdigit():
        limit = int(args[0])
    entries = shell.get_history(limit)
    for index, line in enumerate(entries, start=1):
        console.print(f"[dim]{index:>4}[/]  {escape(line)}")
    return 0


def cmd_theme(args: List[str], shell) -> int:
    if not args:
        current = shell.config.get("theme", "gbit")
        console.print(f"tema atual: [cyan bold]{current}[/]")
        console.print("disponiveis: " + ", ".join(theme_names()))
        return 0
    name = args[0]
    if name not in theme_names():
        console.print(f"[red]tema desconhecido:[/] {name}")
        console.print("disponiveis: " + ", ".join(theme_names()))
        return 1
    shell.set_theme(name)
    console.print(f"[green]tema alterado para[/] [cyan bold]{name}[/]")
    return 0


SETTABLE = {
    "theme": "tema de cores: " + " | ".join(theme_names()),
    "tag": "texto do selo no prompt",
    "prompt_style": "full | minimal",
    "show_git": "mostrar o estado do git",
    "show_venv": "mostrar o ambiente virtual do Python",
    "show_node": "mostrar o selo 'node' em projetos com package.json",
    "show_time": "mostrar a hora no prompt",
    "suggestions": "sugestao em cinza a partir do historico",
    "syntax_highlight": "colorir a linha enquanto digita",
}


def _persist_setting(key: str, value) -> bool:
    """Write or replace a `set key=value` line in ~/.gbitrc."""
    from .config import RC_FILE
    raw = "true" if value is True else "false" if value is False else str(value)
    line = f"set {key}={raw}"
    try:
        text = RC_FILE.read_text(encoding="utf-8") if RC_FILE.exists() else ""
        lines = text.splitlines()
        for index, existing in enumerate(lines):
            stripped = existing.strip()
            if stripped.startswith("set ") and "=" in stripped:
                name = stripped[4:].split("=", 1)[0].strip()
                if name == key:
                    lines[index] = line
                    break
        else:
            lines.append(line)
        RC_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def cmd_set(args: List[str], shell) -> int:
    """set [--save] opcao=valor | set opcao | set"""
    save = False
    rest = []
    for token in args:
        if token in ("--save", "-s"):
            save = True
        else:
            rest.append(token)

    if not rest:
        table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        table.add_column("Opcao", style="cyan bold")
        table.add_column("Valor")
        table.add_column("Descricao", style="dim")
        for key, desc in SETTABLE.items():
            value = shell.config.get(key, "")
            shown = "true" if value is True else "false" if value is False else str(value)
            table.add_row(key, shown, desc)
        console.print(table)
        console.print("[dim]uso: [bold]set show_node=false[/bold] · "
                      "acrescente [bold]--save[/bold] para gravar no ~/.gbitrc[/]")
        return 0

    body = " ".join(rest)
    if "=" not in body:
        key = body.strip()
        if key not in SETTABLE:
            console.print(f"[red]opcao desconhecida:[/] {escape(key)}")
            return 1
        console.print(f"{key} = {shell.config.get(key)}")
        return 0

    key, raw = body.split("=", 1)
    key = key.strip()
    raw = raw.strip().strip('"').strip("'")
    if key not in SETTABLE:
        console.print(f"[red]opcao desconhecida:[/] {escape(key)}")
        console.print("disponiveis: " + ", ".join(SETTABLE))
        return 1

    from .config import _coerce
    value = _coerce(raw)

    if key == "theme":
        if raw not in theme_names():
            console.print(f"[red]tema desconhecido:[/] {escape(raw)}")
            return 1
        shell.set_theme(raw)
    elif key == "prompt_style" and raw not in ("full", "minimal"):
        console.print("[red]prompt_style aceita full ou minimal[/]")
        return 1
    else:
        shell.config.set(key, value)

    shown = "true" if value is True else "false" if value is False else escape(str(value))
    console.print(f"[green]{key}[/] = [cyan bold]{shown}[/]")
    if save:
        if _persist_setting(key, value):
            console.print("[dim]gravado no ~/.gbitrc[/]")
        else:
            console.print("[yellow]nao consegui gravar no ~/.gbitrc[/]")
    return 0


def cmd_reload(args: List[str], shell) -> int:
    shell.reload_config()
    console.print("[green]~/.gbitrc recarregado[/]")
    return 0


def cmd_gbit(args: List[str], shell) -> int:
    """Mostra o banner do GBit Shell dentro da sessao.

    Uso:
        gbit          -> mostra o banner
        gbit shell    -> mostra o banner (alias)
        gbit --help   -> mostra ajuda rapida
    """
    if "--help" in args or "-h" in args:
        console.print("[bold cyan]gbit[/] [dim]ou[/] [bold cyan]gbit shell[/]")
        console.print("  Mostra o banner do GBit Shell")
        console.print("  Exemplo: [cyan]gbit[/]  ou  [cyan]gbit shell[/]")
        return 0

    # Ignora "shell" se presente (gbit shell == gbit)
    clean = [a for a in args if a != "shell"]
    if clean:
        console.print(f"[yellow]gbit: argumento desconhecido:[/] {escape(' '.join(clean))}")
        console.print("[dim]Use[/] [cyan]gbit[/] [dim]ou[/] [cyan]gbit shell[/] [dim]para ver o banner[/]")
        return 1

    # Exibe o banner forçando (ignora GBIT_VSCODE e no_banner)
    shell.print_banner(force=True)
    return 0


def cmd_exit(args: List[str], shell) -> int:
    if not shell.confirm_exit():
        return 1
    shell.running = False
    return 0


# ======================================================================
# GitHub commands
# ======================================================================

def cmd_ghpush(args: List[str], shell) -> int:
    """One-shot: init + gitignore + commit + create repo + push."""
    cwd = os.getcwd()
    message = "chore: update via GBit Shell"
    repo_name = Path(cwd).name
    private = False
    remote_url = None
    description = ""

    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-m", "--message") and index + 1 < len(args):
            message = args[index + 1]; index += 2; continue
        if arg in ("-n", "--name") and index + 1 < len(args):
            repo_name = args[index + 1]; index += 2; continue
        if arg in ("-d", "--description") and index + 1 < len(args):
            description = args[index + 1]; index += 2; continue
        if arg in ("-p", "--private"):
            private = True; index += 1; continue
        if arg == "--remote" and index + 1 < len(args):
            remote_url = args[index + 1]; index += 2; continue
        if not arg.startswith("-") and message == "chore: update via GBit Shell":
            message = arg; index += 1; continue
        index += 1

    if not gh.has_git():
        console.print("[red]git nao encontrado no PATH.[/] Instale o git primeiro.")
        return 1

    repo_name = gh.sanitize_repo_name(repo_name)
    console.print(f"[bold cyan][GBit][/] publicando [bold]{Path(cwd).name}[/] no GitHub\n")

    created, msg = gh.ensure_repo(cwd)
    console.print(f"  {'[green]+[/]' if created else '[dim]=[/]'} {msg}")

    if gh.ensure_gitignore(cwd):
        console.print("  [green]+[/] .gitignore criado")

    committed, msg = gh.stage_and_commit(cwd, message)
    console.print(f"  {'[green]+[/]' if committed else '[dim]=[/]'} {msg}")

    info = get_git_info(cwd)
    has_remote = bool(info and info.get("has_remote"))

    if remote_url:
        ok, msg = gh.set_remote(cwd, remote_url)
        console.print(f"  {'[green]+[/]' if ok else '[red]x[/]'} {msg}")
        if not ok:
            return 1
    elif not has_remote:
        ok, msg = gh.create_github_repo(cwd, repo_name, private, description)
        console.print(f"  {'[green]+[/]' if ok else '[red]x[/]'} {msg}")
        if not ok:
            console.print(
                "\n[yellow]Alternativa:[/] crie o repo no site e rode:\n"
                f"  [bold]ghpush --remote https://github.com/SEU_USUARIO/{repo_name}.git[/]"
            )
            return 1
    else:
        console.print(f"  [dim]=[/] remote origin ja configurado")

    console.print()
    ok, msg = gh.push(cwd)
    console.print(f"\n  {'[green]+[/]' if ok else '[red]x[/]'} {msg}")
    if not ok:
        return 1

    url = gh.repo_web_url(cwd)
    if url:
        console.print(Panel(f"[bold green]Publicado[/]\n[cyan]{escape(url)}[/]",
                            border_style="green", box=box.ROUNDED))
    else:
        console.print(Panel("[bold green]Publicado[/]", border_style="green",
                            box=box.ROUNDED))
    return 0


def cmd_ghinit(args: List[str], shell) -> int:
    """Create a GitHub repo for the current folder (no push)."""
    cwd = os.getcwd()
    name = gh.sanitize_repo_name(args[0] if args and not args[0].startswith("-")
                                 else Path(cwd).name)
    private = any(a in ("-p", "--private") for a in args)
    gh.ensure_repo(cwd)
    gh.ensure_gitignore(cwd)
    ok, msg = gh.create_github_repo(cwd, name, private)
    console.print(f"{'[green]+[/]' if ok else '[red]x[/]'} {msg}")
    return 0 if ok else 1


def cmd_ghclone(args: List[str], shell) -> int:
    if not args:
        console.print("[yellow]uso: ghclone <usuario/repo | url> [destino][/]")
        return 1
    dest = args[1] if len(args) > 1 else None
    ok, msg = gh.clone(args[0], dest)
    console.print(f"{'[green]+[/]' if ok else '[red]x[/]'} {msg}")
    return 0 if ok else 1


def cmd_ghopen(args: List[str], shell) -> int:
    ok, msg = gh.open_repo_in_browser(os.getcwd())
    console.print(f"{'[green]+[/]' if ok else '[red]x[/]'} {msg}")
    return 0 if ok else 1


def cmd_ghstatus(args: List[str], shell) -> int:
    cwd = os.getcwd()
    info = get_git_info(cwd)
    if not info:
        console.print("[yellow]este diretorio nao e um repositorio git[/]")
        console.print("[dim]dica: rode [bold]ghpush[/bold] para publicar no GitHub[/]")
        return 1
    table = Table(box=box.ROUNDED, show_header=False, border_style="cyan")
    table.add_column("Campo", style="dim")
    table.add_column("Valor")
    table.add_row("Branch", f"[bold cyan]{escape(info['branch'])}[/]")
    table.add_row("Raiz", escape(info["root"]))
    table.add_row("Remote", escape(info["remote"]) if info["remote"] else "[dim]nenhum[/]")
    state = "[green]limpo[/]" if not info["dirty"] else (
        f"[yellow]staged {info['staged']} / modificados {info['unstaged']} / novos {info['untracked']}[/]")
    table.add_row("Estado", state)
    table.add_row("Sincronia", f"↑{info['ahead']} ↓{info['behind']}")
    gh_user = gh.gh_username() if gh.has_gh() else None
    table.add_row("gh CLI", f"[green]{gh_user}[/]" if gh_user else "[dim]nao autenticado[/]")
    console.print(table)
    return 0


# ======================================================================
# Developer utilities
# ======================================================================

def cmd_serve(args: List[str], shell) -> int:
    port = 8000
    directory = "."
    for arg in args:
        if arg.isdigit():
            port = int(arg)
        elif not arg.startswith("-"):
            directory = arg
    console.print(f"[bold cyan][GBit][/] servindo [bold]{directory}[/] em "
                  f"[cyan]http://localhost:{port}[/]  [dim](Ctrl+C para parar)[/]")
    console.print("[yellow]Aviso: servidor sem autenticacao — use apenas em rede local.[/]")
    try:
        subprocess.run([sys.executable, "-m", "http.server", str(port),
                        "--directory", directory])
    except KeyboardInterrupt:
        console.print("\n[dim]servidor encerrado[/]")
    return 0


def cmd_ports(args: List[str], shell) -> int:
    try:
        import psutil
    except ImportError:
        console.print("[yellow]psutil nao instalado. Rode: pip install psutil[/]")
        return 1
    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("Porta", justify="right", style="cyan bold")
    table.add_column("PID", justify="right")
    table.add_column("Processo")
    table.add_column("Endereco", style="dim")
    rows = []
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            name = "?"
            if conn.pid:
                try:
                    name = psutil.Process(conn.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    name = "?"
            rows.append((conn.laddr.port, conn.pid or "-", name, conn.laddr.ip))
    except (psutil.AccessDenied, PermissionError):
        console.print("[yellow]permissao insuficiente para listar portas[/]")
        return 1
    for port, pid, name, ip in sorted(set(rows)):
        table.add_row(str(port), str(pid), name, ip)
    console.print(table if rows else "[dim]nenhuma porta em escuta[/]")
    return 0


def cmd_killport(args: List[str], shell) -> int:
    if not args or not args[0].isdigit():
        console.print("[yellow]uso: killport <porta>[/]")
        return 1
    port = int(args[0])
    try:
        import psutil
    except ImportError:
        console.print("[yellow]psutil nao instalado. Rode: pip install psutil[/]")
        return 1
    killed = []
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and \
                    conn.laddr.port == port and conn.pid:
                try:
                    proc = psutil.Process(conn.pid)
                    name = proc.name()
                    proc.terminate()
                    killed.append(f"{name} (pid {conn.pid})")
                except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                    console.print(f"[red]nao consegui encerrar pid {conn.pid}: {exc}[/]")
    except (psutil.AccessDenied, PermissionError):
        console.print("[yellow]permissao insuficiente[/]")
        return 1
    if killed:
        for item in killed:
            console.print(f"[green]encerrado:[/] {item}")
        return 0
    console.print(f"[dim]nada escutando na porta {port}[/]")
    return 1


def cmd_sysinfo(args: List[str], shell) -> int:
    table = Table(box=box.ROUNDED, show_header=False, border_style="cyan",
                  title="[bold cyan]Sistema[/]")
    table.add_column("Campo", style="dim")
    table.add_column("Valor")
    table.add_row("GBit Shell", f"v{__version__}")
    table.add_row("Sistema", f"{platform.system()} {platform.release()}")
    table.add_row("Arquitetura", platform.machine())
    table.add_row("Python", platform.python_version())
    table.add_row("Host", socket.gethostname())
    table.add_row("Shell nativo", os.environ.get("SHELL") or os.environ.get("COMSPEC", "?"))
    for tool in ("git", "gh", "node", "npm", "python3", "docker"):
        path = shutil.which(tool)
        if path:
            table.add_row(tool, f"[green]{path}[/]")
    try:
        import psutil
        mem = psutil.virtual_memory()
        table.add_row("CPU", f"{psutil.cpu_count(logical=True)} nucleos / {psutil.cpu_percent(0.1)}%")
        table.add_row("Memoria", f"{_human_size(mem.used)} / {_human_size(mem.total)} ({mem.percent}%)")
    except ImportError:
        pass
    console.print(table)
    return 0


def cmd_help(args: List[str], shell) -> int:
    if args:
        return _help_topic(args[0])

    console.print(Panel(
        "[bold cyan]GBit Shell[/] " + f"[dim]v{__version__}[/]\n"
        "[dim]Terminal moderno para desenvolvimento[/]",
        border_style="cyan", box=box.ROUNDED))

    groups = [
        ("Navegacao", [
            ("cd <dir>", "muda de diretorio (cd - volta ao anterior)"),
            ("ls [-la]", "lista arquivos com cores"),
            ("pwd", "mostra o diretorio atual"),
            ("cat <arq>", "exibe um arquivo"),
            ("mkdir / touch / rm / cp / mv", "operacoes de arquivo"),
            ("which <cmd>", "localiza um comando"),
        ]),
        ("GitHub", [
            ("ghpush [-m msg]", "init + commit + cria repo + push, tudo de uma vez"),
            ("ghpush -p", "o mesmo, mas repositorio privado"),
            ("ghpush --remote <url>", "usa um remote existente em vez de criar"),
            ("ghinit [nome]", "cria o repo no GitHub sem enviar"),
            ("ghclone user/repo", "clona um repositorio"),
            ("ghstatus", "estado do repo, remote e autenticacao"),
            ("ghopen", "abre o repositorio no navegador"),
        ]),
        ("Desenvolvimento", [
            ("serve [porta]", "servidor HTTP estatico na pasta atual"),
            ("ports", "lista portas TCP em escuta"),
            ("killport <porta>", "encerra quem ocupa a porta"),
            ("sysinfo", "informacoes do sistema e ferramentas"),
        ]),
        ("Jobs", [
            ("cmd &", "roda em segundo plano"),
            ("Ctrl+Z", "suspende o comando em execucao"),
            ("jobs [-a]", "lista jobs ativos (ou todos)"),
            ("fg [%n]", "retoma o job em primeiro plano"),
            ("bg [%n]", "continua o job em segundo plano"),
            ("kill [-9] %n", "envia sinal a um job ou pid"),
            ("wait [%n]", "aguarda o termino dos jobs"),
            ("disown [%n]", "tira o job da lista, sem matar"),
        ]),
        ("Projeto", [
            ("project init", "cria gbit.json na pasta atual"),
            ("project", "mostra aliases, env e scripts do projeto"),
            ("project scripts", "lista apenas os scripts"),
            ("run <script>", "executa um script do gbit.json"),
        ]),
        ("Shell", [
            ("alias [n=cmd]", "lista ou define atalhos"),
            ("export K=V", "define variavel de ambiente"),
            ("env [filtro]", "lista variaveis (segredos ocultos)"),
            ("history [n]", "historico de comandos"),
            ("theme [nome]", "troca o tema de cores"),
            ("set [op=valor]", "ajusta o prompt (use --save p/ gravar)"),
            ("reload", "recarrega ~/.gbitrc"),
            ("vscode", "instala a extensao GBit Shell no VS Code"),
            ("gbit shell", "mostra o banner do GBit Shell"),
            ("clear", "limpa a tela"),
            ("exit", "sai do shell"),
        ]),
    ]

    for title, items in groups:
        table = Table(box=box.SIMPLE, show_header=False, title=f"[bold]{title}[/]",
                      title_justify="left", padding=(0, 1))
        table.add_column("Comando", style="cyan bold", no_wrap=True)
        table.add_column("Descricao", style="white")
        for name, desc in items:
            table.add_row(name, desc)
        console.print(table)

    console.print("\n[dim]Qualquer outro comando e repassado ao sistema "
                  "(git, npm, node, python...).[/]")
    console.print("[dim]Atalhos: Tab completa · ↑↓ historico · Ctrl+R busca · "
                  "Ctrl+L limpa · Ctrl+C cancela · Ctrl+D sai[/]")
    console.print("[dim]Ajuda detalhada: [bold]help config[/bold] \u00b7 "
                  "[bold]help github[/bold] \u00b7 [bold]help jobs[/bold] \u00b7 "
                  "[bold]help project[/bold] \u00b7 [bold]help vscode[/bold] \u00b7 "
                  "[bold]help keys[/bold][/]")
    return 0


def _help_topic(topic: str) -> int:
    topic = topic.lower()
    if topic == "config":
        console.print(Panel(
            "O arquivo [bold cyan]~/.gbitrc[/] e lido na inicializacao.\n\n"
            "[bold]Sintaxe:[/]\n"
            "  [cyan]alias[/] ll=ls -la          define um atalho\n"
            "  [cyan]export[/] EDITOR=code       variavel de ambiente\n"
            "  [cyan]set[/] theme=ocean          opcao do shell\n"
            "  [cyan]run[/] sysinfo              comando na inicializacao\n"
            "  [dim]# comentario[/]\n\n"
            "[bold]Opcoes de set:[/]\n"
            "  theme       gbit | ocean | mono\n"
            "  tag         texto do selo no prompt (padrao GBIT)\n"
            "  show_git    true | false\n"
            "  show_venv   true | false\n"
            "  show_time   true | false\n"
            "  prompt_style  full | minimal\n"
            "  suggestions true | false\n\n"
            "[dim]Por projeto use um [bold]gbit.json[/bold] na raiz do repo "
            "(veja [bold]help project[/bold]).[/]",
            title="[bold]Configuracao[/]", border_style="cyan", box=box.ROUNDED))
        return 0
    if topic == "github":
        console.print(Panel(
            "[bold]ghpush[/] faz tudo em um comando:\n"
            "  1. [cyan]git init[/] se necessario\n"
            "  2. cria [cyan].gitignore[/] se nao existir\n"
            "  3. [cyan]git add -A[/] + [cyan]commit[/]\n"
            "  4. cria o repositorio no GitHub (via gh CLI)\n"
            "  5. [cyan]git push -u origin[/]\n\n"
            "[bold]Requisito:[/] o [cyan]gh[/] CLI autenticado.\n"
            "  Instale: [dim]https://cli.github.com[/]\n"
            "  Autentique: [bold]gh auth login[/]\n\n"
            "[bold]Sem o gh CLI[/] crie o repo no site e use:\n"
            "  [bold]ghpush --remote https://github.com/usuario/repo.git[/]\n\n"
            "[bold]Exemplos:[/]\n"
            "  ghpush\n"
            "  ghpush -m \"feat: nova pagina\"\n"
            "  ghpush -n meu-site -p\n"
            "  ghclone Gislaine-programadora/gbit-container",
            title="[bold]GitHub[/]", border_style="cyan", box=box.ROUNDED))
        return 0
    if topic in ("keys", "atalhos"):
        console.print(Panel(
            "  [cyan]Tab[/]         completa comando, caminho, branch, script npm\n"
            "  [cyan]↑ ↓[/]         navega no historico\n"
            "  [cyan]→[/]           aceita a sugestao em cinza\n"
            "  [cyan]Ctrl+R[/]      busca reversa no historico\n"
            "  [cyan]Ctrl+A / E[/]  inicio / fim da linha\n"
            "  [cyan]Ctrl+W[/]      apaga a palavra anterior\n"
            "  [cyan]Ctrl+U[/]      apaga a linha\n"
            "  [cyan]Ctrl+L[/]      limpa a tela\n"
            "  [cyan]Ctrl+C[/]      cancela o comando atual\n"
            "  [cyan]Ctrl+Z[/]      suspende o comando (retome com fg)\n"
            "  [cyan]Ctrl+D[/]      sai do shell",
            title="[bold]Atalhos de teclado[/]", border_style="cyan", box=box.ROUNDED))
        return 0
    if topic in ("jobs", "job"):
        console.print(Panel(
            "[bold]Rodar em segundo plano[/]\n"
            "  [cyan]npm run dev &[/]        inicia e devolve o prompt\n"
            "  [cyan]jobs[/]                 lista o que esta rodando\n\n"
            "[bold]Suspender e retomar[/]\n"
            "  [cyan]Ctrl+Z[/]               suspende o comando atual\n"
            "  [cyan]fg[/]                   retoma o ultimo job em primeiro plano\n"
            "  [cyan]fg %2[/]                retoma o job numero 2\n"
            "  [cyan]bg %2[/]                continua o job 2 em segundo plano\n\n"
            "[bold]Encerrar[/]\n"
            "  [cyan]kill %1[/]              envia SIGTERM ao job 1\n"
            "  [cyan]kill -9 %1[/]           forca o encerramento\n"
            "  [cyan]kill -l[/]              lista os sinais\n"
            "  [cyan]wait[/]                 aguarda todos terminarem\n"
            "  [cyan]disown %1[/]            tira da lista sem matar o processo\n\n"
            "[bold]Referencias[/] [dim]%+ ou nada = job atual, %- = anterior, "
            "%N = numero, ou o inicio do comando[/]\n\n"
            "[yellow]Windows:[/] [dim]nao existe Ctrl+Z para processos, entao "
            "apenas [bold]&[/bold] e [bold]jobs[/bold] funcionam; use WSL para "
            "suspender e retomar.[/]",
            title="[bold]Controle de jobs[/]", border_style="cyan", box=box.ROUNDED))
        return 0
    if topic in ("vscode", "vs-code", "extension"):
        console.print(Panel(
            "[bold]Instalacao da extensao VS Code[/]\n\n"
            "O GBit Shell traz uma extensao que o adiciona como perfil\n"
            "de terminal integrado no VS Code.\n\n"
            "[bold]Instalacao automatica:[/]\n"
            "  [cyan]vscode[/]            gera o .vsix e instala no VS Code\n"
            "  [cyan]vscode --build[/]    so gera o .vsix (nao instala)\n\n"
            "[bold]O que a extensao faz:[/]\n"
            "  - Perfil de terminal [bold]GBit Shell[/] na lista de terminais\n"
            "  - Comando para publicar no GitHub direto do painel SCM\n"
            "  - Configuracoes de tema, selo e banner\n"
            "  - Verificacao de instalacao (doctor)\n\n"
            "[bold]Apos instalar:[/]\n"
            "  1. Recarregue a janela: [cyan]Ctrl+Shift+P[/] → [dim]Developer: Reload Window[/]\n"
            "  2. Abra o terminal e escolha [bold]GBit Shell[/] na lista de perfis\n\n"
            "[bold]Instalacao manual:[/]\n"
            "  [cyan]code --install-extension ~/.gbit-shell/gislaine.gbit-shell-terminal-1.0.0.vsix[/]",
            title="[bold]Extensao VS Code[/]", border_style="cyan", box=box.ROUNDED))
        return 0
    if topic in ("project", "projeto", "gbit.json"):
        console.print(Panel(
            f"Um [bold cyan]{PROJECT_FILE}[/] na raiz do repo define aliases, "
            "variaveis e scripts daquele projeto.\n"
            "O shell procura da pasta atual para cima a cada [cyan]cd[/].\n\n"
            "[bold]Comandos[/]\n"
            "  [cyan]project init[/]      cria o arquivo (detecta npm, python, cargo, go)\n"
            "  [cyan]project[/]           mostra o que esta ativo\n"
            "  [cyan]project scripts[/]   lista os scripts\n"
            "  [cyan]project reload[/]    le o arquivo de novo\n"
            "  [cyan]run <script>[/]      executa um script na raiz do projeto\n\n"
            "[bold]Exemplo[/]\n"
            "[dim]{\n"
            '  "name": "web3-hub",\n'
            '  "tag": "WEB3",\n'
            '  "theme": "ocean",\n'
            '  "aliases": { "dev": "npm run dev" },\n'
            '  "env": { "NODE_ENV": "development" },\n'
            '  "scripts": {\n'
            '    "deploy": ["npm run build", "ghpush -m deploy"]\n'
            "  }\n"
            "}[/]\n\n"
            "[bold]Precedencia[/] [dim]aliases do projeto vencem os do ~/.gbitrc; "
            "o mesmo vale para env.[/]\n"
            "[bold]Seguranca[/] [dim]nada e executado ao abrir a pasta - scripts "
            "rodam somente quando voce chama [bold]run[/bold].[/]",
            title="[bold]Configuracao por projeto[/]", border_style="cyan",
            box=box.ROUNDED))
        return 0
    console.print(f"[yellow]sem ajuda para '{topic}'.[/] "
                  "Topicos: config, github, jobs, project, vscode, keys")
    return 1


def cmd_weather(args: List[str], shell) -> int:
    console.print("[dim]Ensolarado, com chance de deploy.[/]")
    return 0


# ======================================================================
# Helpers / registry
# ======================================================================

def _human_size(num: float) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if abs(num) < 1024:
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}P"


BUILTINS: Dict[str, Callable] = {
    # navigation / fs
    "cd": cmd_cd,
    "pwd": cmd_pwd,
    "ls": cmd_ls,
    "dir": cmd_ls,
    "cat": cmd_cat,
    "mkdir": cmd_mkdir,
    "touch": cmd_touch,
    "rm": cmd_rm,
    "cp": cmd_cp,
    "mv": cmd_mv,
    "which": cmd_which,
    "clear": cmd_clear,
    # shell env
    "alias": cmd_alias,
    "unalias": cmd_unalias,
    "export": cmd_export,
    "unset": cmd_unset,
    "env": cmd_env,
    "history": cmd_history,
    "theme": cmd_theme,
    "set": cmd_set,
    "reload": cmd_reload,
    "gbit": cmd_gbit,
    "help": cmd_help,
    "exit": cmd_exit,
    "quit": cmd_exit,
    # github
    "ghpush": cmd_ghpush,
    "ghinit": cmd_ghinit,
    "ghclone": cmd_ghclone,
    "ghopen": cmd_ghopen,
    "ghstatus": cmd_ghstatus,
    # dev utils
    "serve": cmd_serve,
    "ports": cmd_ports,
    "killport": cmd_killport,
    "sysinfo": cmd_sysinfo,
    "weather": cmd_weather,

    # job control
    "jobs": cmd_jobs,
    "fg": cmd_fg,
    "bg": cmd_bg,
    "kill": cmd_kill,
    "wait": cmd_wait,
    "disown": cmd_disown,

    # per-project config
    "project": cmd_project,
    "run": cmd_run,

    # vs code extension
    "vscode": cmd_vscode,
}
