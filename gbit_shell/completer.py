'''
GBit Shell - Tab completion
Context-aware: commands on the first word, paths afterwards,
plus git subcommand/branch completion and npm script completion.
'''
import os
import json
import shutil
from pathlib import Path
from typing import Iterable, List, Optional

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from .gitinfo import find_repo_root

GIT_SUBCOMMANDS = [
    "add", "branch", "checkout", "cherry-pick", "clone", "commit", "config",
    "diff", "fetch", "init", "log", "merge", "mv", "pull", "push", "rebase",
    "remote", "reset", "restore", "revert", "rm", "show", "stash", "status",
    "switch", "tag", "worktree",
]

BUILTIN_HELP = {
    "cd": "change directory",
    "pwd": "print working directory",
    "ls": "list directory contents",
    "exit": "leave the shell",
    "help": "show help",
    "alias": "define or list aliases",
    "unalias": "remove an alias",
    "export": "set an environment variable",
    "unset": "remove an environment variable",
    "env": "list environment variables",
    "clear": "clear the screen",
    "history": "show command history",
    "which": "locate a command",
    "cat": "print a file",
    "mkdir": "create a directory",
    "touch": "create an empty file",
    "rm": "remove files",
    "cp": "copy files",
    "mv": "move files",
    "theme": "change the color theme",
    "reload": "reload ~/.gbitrc",
    "ghpush": "publish this folder to GitHub",
    "ghinit": "create a GitHub repo and push",
    "ghclone": "clone a GitHub repo",
    "ghopen": "open the repo in the browser",
    "ghstatus": "show repo + remote status",
    "serve": "serve the current folder over HTTP",
    "ports": "list listening TCP ports",
    "killport": "kill whatever listens on a port",
    "sysinfo": "show system information",
    "jobs": "list background and suspended jobs",
    "fg": "resume a job in the foreground",
    "bg": "resume a job in the background",
    "kill": "send a signal to a job or pid",
    "wait": "wait for background jobs",
    "project": "inspect or create gbit.json",
    "run": "run a script from gbit.json",
    "weather": "nothing. it is a shell, not a forecast",
}


class GBitCompleter(Completer):
    """Main completer used by the interactive shell."""

    def __init__(self, config, builtins: Optional[Iterable[str]] = None,
                 shell=None):
        self.config = config
        self.shell = shell
        self.builtins = sorted(set(builtins or []) | set(BUILTIN_HELP))
        self._path_cache: Optional[List[str]] = None

    # ----------------------------------------------------------------
    def _aliases(self) -> dict:
        if self.shell is not None:
            return self.shell.all_aliases()
        return dict(self.config.aliases)

    def _project_scripts(self) -> dict:
        if self.shell is None:
            return {}
        return self.shell.project.scripts

    def _job_specs(self):
        """Yield (spec, description) for the active jobs."""
        if self.shell is None:
            return
        for job in self.shell.jobs.active():
            yield f"%{job.id}", f"{job.state} - {job.command[:40]}"

    # ----------------------------------------------------------------
    def _system_commands(self) -> List[str]:
        """Executables found on PATH (cached for the session)."""
        if self._path_cache is not None:
            return self._path_cache
        found = set()
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory or not os.path.isdir(directory):
                continue
            try:
                for entry in os.scandir(directory):
                    if entry.is_file() or entry.is_symlink():
                        name = entry.name
                        if os.name == "nt":
                            stem, ext = os.path.splitext(name)
                            if ext.lower() in (".exe", ".cmd", ".bat", ".com"):
                                found.add(stem)
                        else:
                            found.add(name)
            except OSError:
                continue
        self._path_cache = sorted(found)
        return self._path_cache

    # ----------------------------------------------------------------
    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()
        words = stripped.split()
        typing_new_word = text.endswith((" ", "\t"))

        # ---- first word: command / alias / builtin ----------------
        if len(words) == 0 or (len(words) == 1 and not typing_new_word):
            word = words[0] if words else ""
            yield from self._complete_command(word)
            return

        head = words[0]
        current = "" if typing_new_word else words[-1]

        # ---- git-aware -------------------------------------------
        if head in ("git", "g"):
            position = len(words) - (0 if typing_new_word else 1)
            if position == 1:
                for sub in GIT_SUBCOMMANDS:
                    if sub.startswith(current):
                        yield Completion(sub, -len(current), display_meta="git")
                return
            sub = words[1]
            if sub in ("checkout", "switch", "merge", "rebase", "branch"):
                for branch in self._git_branches():
                    if branch.startswith(current):
                        yield Completion(branch, -len(current), display_meta="branch")
                return

        # ---- npm / yarn / pnpm scripts ----------------------------
        if head in ("npm", "yarn", "pnpm") and len(words) >= 2:
            if words[1] in ("run", "run-script") or head in ("yarn", "pnpm"):
                for script in self._npm_scripts():
                    if script.startswith(current):
                        yield Completion(script, -len(current), display_meta="script")

        # ---- gbit.json scripts ------------------------------------
        if head == "run":
            for name, steps in sorted(self._project_scripts().items()):
                if name.startswith(current):
                    preview = " && ".join(steps)
                    yield Completion(name, -len(current),
                                     display_meta=preview[:50])
            return

        # ---- project subcommands ----------------------------------
        if head == "project":
            for name, meta in (("init", "cria gbit.json"),
                               ("info", "mostra o projeto"),
                               ("scripts", "lista scripts"),
                               ("path", "caminho do arquivo"),
                               ("reload", "recarrega")):
                if name.startswith(current):
                    yield Completion(name, -len(current), display_meta=meta)
            return

        # ---- job specs -------------------------------------------
        if head in ("fg", "bg", "kill", "wait"):
            for spec, meta in self._job_specs():
                if spec.startswith(current) or not current:
                    yield Completion(spec, -len(current), display_meta=meta)
            if head != "kill":
                return

        # ---- help topics -----------------------------------------
        if head == "help":
            for name in ("config", "github", "jobs", "project", "keys"):
                if name.startswith(current):
                    yield Completion(name, -len(current), display_meta="topico")
            return

        # ---- theme ------------------------------------------------
        if head == "theme":
            from .styles import theme_names
            for name in theme_names():
                if name.startswith(current):
                    yield Completion(name, -len(current), display_meta="theme")
            return

        # ---- fallback: filesystem paths ---------------------------
        yield from self._complete_path(current)

    # ----------------------------------------------------------------
    def _complete_command(self, word: str):
        seen = set()
        for name in self.builtins:
            if name.startswith(word) and name not in seen:
                seen.add(name)
                yield Completion(name, -len(word),
                                 display_meta=BUILTIN_HELP.get(name, "builtin"))
        aliases = self._aliases()
        for name in sorted(aliases):
            if name.startswith(word) and name not in seen:
                seen.add(name)
                yield Completion(name, -len(word),
                                 display_meta=f"alias \u2192 {aliases[name]}")
        for name in self._system_commands():
            if name.startswith(word) and name not in seen:
                seen.add(name)
                yield Completion(name, -len(word), display_meta="command")
        # also offer local paths (./script.sh)
        if word.startswith((".", "/", "~")):
            yield from self._complete_path(word)

    # ----------------------------------------------------------------
    def _complete_path(self, word: str):
        expanded = os.path.expanduser(word)
        if expanded.endswith(os.sep) or expanded == "":
            directory = expanded or "."
            prefix = ""
        else:
            directory = os.path.dirname(expanded) or "."
            prefix = os.path.basename(expanded)
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name.lower())
        except OSError:
            return
        for entry in entries:
            if not entry.name.startswith(prefix):
                continue
            if entry.name.startswith(".") and not prefix.startswith("."):
                continue
            suffix = "/" if entry.is_dir() else ""
            yield Completion(
                entry.name + suffix,
                -len(prefix),
                display_meta="dir" if entry.is_dir() else "file",
            )

    # ----------------------------------------------------------------
    def _git_branches(self) -> List[str]:
        root = find_repo_root(os.getcwd())
        if not root:
            return []
        heads = Path(root) / ".git" / "refs" / "heads"
        names = []
        if heads.is_dir():
            for path in heads.rglob("*"):
                if path.is_file():
                    names.append(str(path.relative_to(heads)).replace(os.sep, "/"))
        return sorted(names)

    # ----------------------------------------------------------------
    def _npm_scripts(self) -> List[str]:
        pkg = Path("package.json")
        if not pkg.is_file():
            return []
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return []
        scripts = data.get("scripts")
        return sorted(scripts) if isinstance(scripts, dict) else []
