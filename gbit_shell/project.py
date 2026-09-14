'''
GBit Shell - Per-project configuration (gbit.json)

A gbit.json anywhere up the directory tree describes the project the
shell is currently inside: local aliases, environment variables, npm-like
scripts and prompt tweaks.

    {
      "name": "web3-hub",
      "tag": "WEB3",
      "theme": "ocean",
      "aliases": { "dev": "npm run dev" },
      "env": { "NODE_ENV": "development" },
      "scripts": { "publish": ["npm test", "ghpush"] }
    }

Security: only known keys are read, and nothing in the file is ever run
automatically. Scripts execute solely when the user types `run <name>`.
'''
import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_FILE = "gbit.json"

# Only these keys are honoured; anything else in the file is ignored.
KNOWN_KEYS = (
    "name", "description", "tag", "theme",
    "aliases", "env", "scripts",
    "show_git", "show_venv", "show_node", "show_time", "prompt_style",
)

# Keys forwarded to the prompt renderer as overrides
PROMPT_KEYS = ("show_git", "show_venv", "show_node", "show_time", "prompt_style")


class ProjectConfig:
    """Parsed gbit.json (or an empty stand-in when there is none)."""

    def __init__(self, path: Optional[Path] = None,
                 data: Optional[Dict[str, Any]] = None,
                 error: Optional[str] = None):
        self.path = path
        self.root = path.parent if path is not None else None
        self.error = error
        self.data: Dict[str, Any] = {}
        if data:
            self.data = {k: v for k, v in data.items() if k in KNOWN_KEYS}

    # ----------------------------------------------------------------
    @property
    def exists(self) -> bool:
        return self.path is not None

    @property
    def name(self) -> str:
        value = self.data.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return self.root.name if self.root is not None else ""

    @property
    def tag(self) -> Optional[str]:
        value = self.data.get("tag")
        return value.strip() if isinstance(value, str) and value.strip() else None

    @property
    def theme(self) -> Optional[str]:
        value = self.data.get("theme")
        return value.strip() if isinstance(value, str) and value.strip() else None

    # ----------------------------------------------------------------
    @property
    def aliases(self) -> Dict[str, str]:
        raw = self.data.get("aliases")
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value) for key, value in raw.items()
                if isinstance(value, (str, int, float))}

    @property
    def env(self) -> Dict[str, str]:
        raw = self.data.get("env")
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, str] = {}
        for key, value in raw.items():
            if isinstance(value, bool):
                out[str(key)] = "1" if value else "0"
            elif isinstance(value, (str, int, float)):
                out[str(key)] = str(value)
        return out

    @property
    def scripts(self) -> Dict[str, List[str]]:
        """Script name -> list of steps (a plain string becomes one step)."""
        raw = self.data.get("scripts")
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, List[str]] = {}
        for key, value in raw.items():
            if isinstance(value, str):
                out[str(key)] = [value]
            elif isinstance(value, list):
                steps = [str(step) for step in value if str(step).strip()]
                if steps:
                    out[str(key)] = steps
        return out

    # ----------------------------------------------------------------
    def prompt_overrides(self) -> Dict[str, Any]:
        return {key: self.data[key] for key in PROMPT_KEYS if key in self.data}

    def __repr__(self) -> str:
        where = str(self.path) if self.path else "none"
        return f"<ProjectConfig {self.name or '-'} {where}>"


# ======================================================================
# Discovery / loading
# ======================================================================

def find_project_file(start: str) -> Optional[Path]:
    """Nearest gbit.json walking up from `start` (None when absent)."""
    try:
        current = Path(start).resolve()
    except OSError:
        return None
    for folder in [current, *current.parents]:
        candidate = folder / PROJECT_FILE
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def load_project(cwd: str) -> ProjectConfig:
    """Always returns a ProjectConfig; check `.exists` and `.error`."""
    path = find_project_file(cwd)
    if path is None:
        return ProjectConfig()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ProjectConfig(path, None, error=f"nao consegui ler: {exc}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return ProjectConfig(path, None,
                             error=f"JSON invalido na linha {exc.lineno}: {exc.msg}")
    if not isinstance(data, dict):
        return ProjectConfig(path, None,
                             error="o arquivo deve conter um objeto JSON")
    return ProjectConfig(path, data)


# ======================================================================
# Scaffolding
# ======================================================================

def detect_project_kind(cwd: str) -> Dict[str, Dict[str, Any]]:
    """Guess useful aliases/scripts from the files in the folder."""
    folder = Path(cwd)
    aliases: Dict[str, str] = {}
    scripts: Dict[str, Any] = {}

    if (folder / "package.json").is_file():
        aliases.update({"dev": "npm run dev", "b": "npm run build",
                        "t": "npm test", "i": "npm install"})
        scripts.update({"build": "npm run build", "test": "npm test"})

    if (folder / "pyproject.toml").is_file() or (folder / "requirements.txt").is_file():
        aliases.setdefault("t", "python -m pytest -q")
        aliases.setdefault("py", "python")
        scripts["test"] = "python -m pytest -q"
        if (folder / "pyproject.toml").is_file():
            scripts["build"] = "python -m build"

    if (folder / "Cargo.toml").is_file():
        aliases.update({"b": "cargo build", "r": "cargo run", "t": "cargo test"})
        scripts["build"] = "cargo build --release"

    if (folder / "go.mod").is_file():
        aliases.update({"r": "go run .", "t": "go test ./..."})
        scripts["build"] = "go build ./..."

    if (folder / "Dockerfile").is_file():
        aliases.setdefault("dbuild", f"docker build -t {folder.name} .")

    return {"aliases": aliases, "scripts": scripts}


def default_template(name: str, detected: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Starter gbit.json content for `project init`."""
    detected = detected or {}
    aliases = dict(detected.get("aliases") or {})
    scripts = dict(detected.get("scripts") or {})
    scripts.setdefault("publish", ["ghpush -m 'chore: update'"])
    return {
        "name": name,
        "description": "",
        # O selo do prompt sempre nasce como "GBIT" simples — nada de
        # derivar do nome do projeto e cortar em 8 caracteres, que gerava
        # tags feias tipo "GBIT-SHE" pra um projeto chamado "gbit-shell".
        # Quem quiser personalizar troca essa linha manualmente depois.
        "tag": "GBIT",
        "aliases": aliases,
        "env": {},
        "scripts": scripts,
    }


def write_project_file(cwd: str, data: Dict[str, Any],
                       force: bool = False) -> Tuple[bool, str]:
    """Create gbit.json. Returns (written, message)."""
    target = Path(cwd) / PROJECT_FILE
    if target.exists() and not force:
        return False, f"{PROJECT_FILE} ja existe (use project init --force)"
    try:
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    except OSError as exc:
        return False, f"nao consegui escrever {PROJECT_FILE}: {exc}"
    return True, f"{PROJECT_FILE} criado"
