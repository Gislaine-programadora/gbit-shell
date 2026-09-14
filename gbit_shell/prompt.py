'''
GBit Shell - Prompt rendering
Builds the prompt_toolkit formatted-text prompt:

    gislaine@DESKTOP-D1QAMU7  GBIT  ~/web3-hub  (main +2 !1)  >
'''
import os
import sys
import getpass
import socket
import time
from pathlib import Path
from typing import List, Tuple, Optional

from .gitinfo import get_git_info

# prompt_toolkit style class names -> see styles.py
FT = List[Tuple[str, str]]


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or os.environ.get("USERNAME") or "user"


def _host() -> str:
    name = os.environ.get("COMPUTERNAME") or socket.gethostname() or "localhost"
    return name.split(".")[0]


def _short_path(cwd: str, max_parts: int = 4) -> str:
    """Render ~ for home and shorten deep paths to .../a/b/c."""
    home = str(Path.home())
    path = cwd
    at_home = False
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        at_home = True
        path = "~" + path[len(home):].replace(os.sep, "/")
    else:
        path = path.replace(os.sep, "/")

    parts = [p for p in path.split("/") if p]
    if len(parts) <= max_parts:
        return path

    tail = "/".join(parts[-max_parts:])
    return ("~/…/" if at_home else "/…/") + tail


def _venv_name() -> Optional[str]:
    venv = os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_DEFAULT_ENV")
    if not venv:
        return None
    name = Path(venv).name
    return name if name not in (".venv", "venv", "env") else Path(venv).parent.name


def _node_project() -> bool:
    return Path("package.json").is_file()


def _normalize_tag(value: str) -> str:
    """Keep old generated labels from leaking into prompts."""
    tag = str(value).strip()
    if tag.upper() in {"GBIT-SHE", "GBIT SHE", "GBIT_SHELL", "GBIT-SHELL"}:
        return "GBIT"
    return tag or "GBIT"


def build_prompt(cwd: str, config, last_exit: int = 0,
                 project=None, jobs=None) -> FT:
    """Return prompt_toolkit formatted text for the main prompt line.

    `project` is a ProjectConfig (gbit.json) whose tag/prompt settings
    override the global config; `jobs` is a JobManager used to show how
    many background/suspended jobs are alive.
    """
    overrides = project.prompt_overrides() if project is not None else {}

    def setting(key, default):
        return overrides.get(key, config.get(key, default))

    style = setting("prompt_style", "full")
    tag = _normalize_tag(config.get("tag", "GBIT"))
    if project is not None and project.tag:
        tag = _normalize_tag(project.tag)
    frags: FT = []

    if style == "minimal":
        frags.append(("class:path", _short_path(cwd, 2)))
        frags.append(("", " "))
        frags.append(("class:arrow.ok" if last_exit == 0 else "class:arrow.err", "❯ "))
        return frags

    # ---- user@host ------------------------------------------------
    frags.append(("class:user", _user()))
    frags.append(("class:at", "@"))
    frags.append(("class:host", _host()))
    frags.append(("", "  "))

    # ---- GBIT tag -------------------------------------------------
    frags.append(("class:tag", f" {tag} "))
    frags.append(("", "  "))

    # ---- path -----------------------------------------------------
    frags.append(("class:path", _short_path(cwd)))

    # ---- git (apenas o nome do branch) -----------------------------
    if setting("show_git", True):
        info = get_git_info(cwd)
        if info:
            frags.append(("", "  "))
            frags.append(("class:git.paren", "("))
            branch_cls = "class:git.dirty" if info["dirty"] else "class:git.clean"
            frags.append((branch_cls, info["branch"]))
            frags.append(("class:git.paren", ")"))


    # ---- venv / node ----------------------------------------------
    if setting("show_venv", True):
        venv = _venv_name()
        if venv:
            frags.append(("", "  "))
            frags.append(("class:venv", f"py:{venv}"))

    if setting("show_node", False) and _node_project():
        frags.append(("", "  "))
        frags.append(("class:node", "node"))

    # ---- time -----------------------------------------------------
    if setting("show_time", False):
        frags.append(("", "  "))
        frags.append(("class:time", time.strftime("%H:%M:%S")))

    # ---- jobs -----------------------------------------------------
    if jobs is not None:
        active = jobs.active()
        if active:
            stopped = sum(1 for job in active if job.state == "stopped")
            label = f"\u2699 {len(active)}"
            if stopped:
                label += f" (\u23f8{stopped})"
            frags.append(("", "  "))
            frags.append(("class:jobs", label))

    # ---- exit code ------------------------------------------------
    if last_exit not in (0, None):
        frags.append(("", "  "))
        frags.append(("class:exitcode", f"✗ {last_exit}"))

    # ---- second line arrow ----------------------------------------
    frags.append(("", "\n"))
    frags.append(("class:arrow.ok" if last_exit == 0 else "class:arrow.err", "❯ "))
    return frags


def build_continuation(width: int, line_number: int, is_soft_wrap: bool) -> FT:
    """Prompt shown for multi-line continuation."""
    return [("class:arrow.cont", "· ")]


def build_rprompt(cwd: str, config) -> FT:
    """Right-aligned prompt: shows elapsed time of last command if slow."""
    return []
