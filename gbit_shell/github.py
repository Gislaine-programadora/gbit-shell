'''
GBit Shell - GitHub integration
Publish a local folder to GitHub in one command.

Strategy:
  1. Prefer the official `gh` CLI when available (handles auth + repo create).
  2. Fall back to plain `git` with a manually supplied remote URL.

No tokens are ever printed or stored by this module.
'''
import os
import re
import json
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional, Tuple, List

from .gitinfo import find_repo_root, get_git_info, invalidate_cache


def has_gh() -> bool:
    return shutil.which("gh") is not None


def has_git() -> bool:
    return shutil.which("git") is not None


def _run(args: List[str], cwd: Optional[str] = None, capture: bool = False,
         timeout: Optional[int] = None) -> Tuple[int, str, str]:
    """Run a command. When capture is False, output streams to the terminal."""
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", f"command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def gh_authenticated() -> bool:
    if not has_gh():
        return False
    code, _, _ = _run(["gh", "auth", "status"], capture=True, timeout=10)
    return code == 0


def gh_username() -> Optional[str]:
    if not has_gh():
        return None
    code, out, _ = _run(["gh", "api", "user", "--jq", ".login"], capture=True, timeout=10)
    return out if code == 0 and out else None


def sanitize_repo_name(name: str) -> str:
    """GitHub repo names allow alnum, dot, dash, underscore."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.")
    return cleaned or "my-project"


DEFAULT_GITIGNORE = """# GBit Shell - default ignores
node_modules/
__pycache__/
*.py[cod]
.venv/
venv/
env/
dist/
build/
*.egg-info/
.DS_Store
Thumbs.db
.env
.env.local
*.log
.gbit/
coverage/
.pytest_cache/
.ruff_cache/
"""


def ensure_gitignore(cwd: str) -> bool:
    """Create a sensible .gitignore when the project has none."""
    path = Path(cwd) / ".gitignore"
    if path.exists():
        return False
    try:
        path.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
        return True
    except OSError:
        return False


def ensure_repo(cwd: str, default_branch: str = "main") -> Tuple[bool, str]:
    """git init when needed. Returns (created, message)."""
    if find_repo_root(cwd):
        return False, "repositorio git ja existe"
    code, _, err = _run(["git", "init", "-b", default_branch], cwd=cwd, capture=True)
    if code != 0:
        # Older git without -b support
        code, _, err = _run(["git", "init"], cwd=cwd, capture=True)
        if code != 0:
            return False, f"git init falhou: {err}"
        _run(["git", "checkout", "-b", default_branch], cwd=cwd, capture=True)
    invalidate_cache()
    return True, f"repositorio git criado (branch {default_branch})"


def stage_and_commit(cwd: str, message: str) -> Tuple[bool, str]:
    """git add -A && git commit. Returns (committed, message)."""
    code, _, err = _run(["git", "add", "-A"], cwd=cwd, capture=True)
    if code != 0:
        return False, f"git add falhou: {err}"

    code, out, _ = _run(["git", "diff", "--cached", "--name-only"], cwd=cwd, capture=True)
    if code == 0 and not out:
        return False, "nada novo para commitar"

    code, _, err = _run(["git", "commit", "-m", message], cwd=cwd, capture=True)
    invalidate_cache()
    if code != 0:
        return False, f"git commit falhou: {err}"
    return True, f"commit criado: {message}"


def create_github_repo(cwd: str, name: str, private: bool = False,
                       description: str = "") -> Tuple[bool, str]:
    """Create the remote repo with gh and wire it as origin."""
    if not has_gh():
        return False, (
            "gh CLI nao encontrado. Instale em https://cli.github.com "
            "ou use: ghpush --remote <url>"
        )
    if not gh_authenticated():
        return False, "gh nao autenticado. Rode: gh auth login"

    args = ["gh", "repo", "create", name,
            "--private" if private else "--public",
            "--source", ".", "--remote", "origin"]
    if description:
        args += ["--description", description]

    code, out, err = _run(args, cwd=cwd, capture=True, timeout=60)
    invalidate_cache()
    if code != 0:
        detail = err or out
        if "already exists" in detail.lower():
            return False, f"o repositorio '{name}' ja existe na sua conta"
        return False, f"falha ao criar repo: {detail}"
    return True, f"repositorio criado: {name}"


def set_remote(cwd: str, url: str, name: str = "origin") -> Tuple[bool, str]:
    code, out, _ = _run(["git", "remote"], cwd=cwd, capture=True)
    existing = out.split() if out else []
    action = "set-url" if name in existing else "add"
    code, _, err = _run(["git", "remote", action, name, url], cwd=cwd, capture=True)
    invalidate_cache()
    if code != 0:
        return False, f"falha ao configurar remote: {err}"
    return True, f"remote {name} -> {url}"


def push(cwd: str, branch: Optional[str] = None, set_upstream: bool = True,
         force: bool = False) -> Tuple[bool, str]:
    """Push the current branch, streaming git output to the terminal."""
    if branch is None:
        info = get_git_info(cwd)
        branch = info["branch"] if info else "main"
    args = ["git", "push"]
    if set_upstream:
        args.append("-u")
    if force:
        args.append("--force-with-lease")
    args += ["origin", branch]
    code, _, _ = _run(args, cwd=cwd, capture=False)
    invalidate_cache()
    if code != 0:
        return False, "push falhou (veja a saida do git acima)"
    return True, f"branch '{branch}' enviada para origin"


def repo_web_url(cwd: str) -> Optional[str]:
    """Convert the origin remote into a browsable https URL.

    Returns None for local/filesystem remotes, which have no web page.
    """
    info = get_git_info(cwd)
    if not info or not info.get("remote"):
        return None
    url = info["remote"]

    if url.startswith("git@"):
        # git@github.com:user/repo.git -> https://github.com/user/repo
        url = url.replace(":", "/", 1).replace("git@", "https://", 1)
    elif url.startswith("ssh://git@"):
        url = url.replace("ssh://git@", "https://", 1)

    if not url.startswith(("http://", "https://")):
        return None  # local path or unsupported scheme

    if url.endswith(".git"):
        url = url[:-4]
    return url


def open_repo_in_browser(cwd: str) -> Tuple[bool, str]:
    url = repo_web_url(cwd)
    if not url:
        return False, "nenhum remote 'origin' configurado"
    try:
        webbrowser.open(url)
    except Exception:
        return False, f"nao consegui abrir o navegador. URL: {url}"
    return True, f"abrindo {url}"


def clone(target: str, dest: Optional[str] = None) -> Tuple[bool, str]:
    """Clone user/repo or a full URL."""
    if "://" not in target and not target.startswith("git@"):
        if target.count("/") == 1:
            target = f"https://github.com/{target}.git"
    args = ["git", "clone", target]
    if dest:
        args.append(dest)
    code, _, _ = _run(args, capture=False)
    if code != 0:
        return False, "clone falhou"
    return True, "clone concluido"
