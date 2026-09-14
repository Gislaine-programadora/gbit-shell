'''
GBit Shell - Configuration management
Reads ~/.gbitrc (INI-like) and manages aliases, env, prompt theme.
'''
import os
import json
from pathlib import Path
from typing import Dict, Any, List

HOME = Path.home()
GBIT_DIR = HOME / ".gbit-shell"
RC_FILE = HOME / ".gbitrc"
HISTORY_FILE = GBIT_DIR / "history"
STATE_FILE = GBIT_DIR / "state.json"

DEFAULT_ALIASES = {
    "ll": "ls -la",
    "la": "ls -a",
    "..": "cd ..",
    "...": "cd ../..",
    "g": "git",
    "gs": "git status",
    "gd": "git diff",
    "gl": "git log --oneline --graph --decorate -20",
    "gco": "git checkout",
    "gb": "git branch",
    "cls": "clear",
}

DEFAULT_CONFIG = {
    "theme": "gbit",
    "prompt_style": "full",
    "show_git": True,
    "show_time": False,
    "show_venv": True,
    "show_node": False,      # selo "node": limpo por padrao, ligue com `set show_node=true`
    "suggestions": True,
    "syntax_highlight": True,
    "tag": "GBIT",
}


class ShellConfig:
    """Holds runtime configuration, aliases and environment overrides."""

    def __init__(self):
        GBIT_DIR.mkdir(parents=True, exist_ok=True)
        self.config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self.aliases: Dict[str, str] = dict(DEFAULT_ALIASES)
        self.env: Dict[str, str] = {}
        self.startup_commands: List[str] = []
        self.load()
        self._apply_env_overrides()

    # ----------------------------------------------------------------
    def _apply_env_overrides(self) -> None:
        """Let the host (e.g. the VS Code extension) override settings."""
        theme = os.environ.get("GBIT_THEME")
        if theme:
            self.config["theme"] = theme
        tag = os.environ.get("GBIT_TAG")
        if tag:
            self.config["tag"] = tag
        for key, var in (("show_node", "GBIT_SHOW_NODE"),
                         ("show_git", "GBIT_SHOW_GIT"),
                         ("show_venv", "GBIT_SHOW_VENV"),
                         ("show_time", "GBIT_SHOW_TIME")):
            raw = os.environ.get(var)
            if raw is not None and raw != "":
                self.config[key] = _coerce(raw)
        if os.environ.get("GBIT_VSCODE") == "1":
            self.config["in_vscode"] = True

    # ----------------------------------------------------------------
    # Loading
    # ----------------------------------------------------------------
    def load(self) -> None:
        """Parse ~/.gbitrc if present.

        Supported lines:
            alias name=value
            export KEY=value
            set option=value
            run <command>       (executed at startup)
            # comment
        """
        if not RC_FILE.exists():
            return
        try:
            text = RC_FILE.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("alias "):
                body = line[6:].strip()
                if "=" in body:
                    name, val = body.split("=", 1)
                    self.aliases[name.strip()] = _unquote(val.strip())
            elif line.startswith("export "):
                body = line[7:].strip()
                if "=" in body:
                    key, val = body.split("=", 1)
                    self.env[key.strip()] = _unquote(val.strip())
            elif line.startswith("set "):
                body = line[4:].strip()
                if "=" in body:
                    key, val = body.split("=", 1)
                    self.config[key.strip()] = _coerce(_unquote(val.strip()))
            elif line.startswith("run "):
                self.startup_commands.append(line[4:].strip())

    def save_rc_if_missing(self) -> bool:
        """Write a starter .gbitrc the first time the shell runs."""
        if RC_FILE.exists():
            return False
        template = (
            "# GBit Shell configuration\n"
            "# Docs: gbit help config\n\n"
            "# --- Prompt -------------------------------------------------\n"
            "set tag=GBIT\n"
            "set show_git=true\n"
            "set show_venv=true\n"
            "set show_node=false      # selo 'node' em projetos com package.json\n"
            "set show_time=false\n\n"
            "# --- Aliases ------------------------------------------------\n"
            "alias ll=ls -la\n"
            "alias gs=git status\n\n"
            "# --- Environment --------------------------------------------\n"
            "# export EDITOR=code\n"
        )
        try:
            RC_FILE.write_text(template, encoding="utf-8")
            return True
        except OSError:
            return False

    # ----------------------------------------------------------------
    # Accessors
    # ----------------------------------------------------------------
    def get(self, key: str, default=None):
        return self.config.get(key, default)

    def set(self, key: str, value) -> None:
        self.config[key] = value

    def resolve_alias(self, cmd_line: str) -> str:
        """Expand a leading alias (one level, avoids infinite loops)."""
        stripped = cmd_line.strip()
        if not stripped:
            return cmd_line
        parts = stripped.split(None, 1)
        head = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        if head in self.aliases:
            expansion = self.aliases[head]
            # Prevent self-recursion (alias ls="ls --color")
            return f"{expansion} {rest}".strip()
        return cmd_line


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _coerce(value: str):
    low = value.lower()
    if low in ("true", "yes", "on", "1"):
        return True
    if low in ("false", "no", "off", "0"):
        return False
    if value.isdigit():
        return int(value)
    return value


def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(state: Dict[str, Any]) -> None:
    try:
        GBIT_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass
