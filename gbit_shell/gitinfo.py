'''
GBit Shell - Fast git repository introspection for the prompt.
Uses short-timeout subprocess calls and caches results per directory+mtime
so the prompt never blocks noticeably.
'''
import os
import subprocess
import time
from pathlib import Path
from typing import Optional, Dict, Any

_CACHE: Dict[str, Any] = {}
_CACHE_TTL = 1.5  # seconds


def _run(args, cwd, timeout=0.6) -> Optional[str]:
    """Run a git command quickly; return stripped stdout or None."""
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def find_repo_root(path: str) -> Optional[str]:
    """Walk up looking for a .git entry. Pure filesystem, no subprocess."""
    try:
        current = Path(path).resolve()
    except OSError:
        return None
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return str(candidate)
    return None


def get_git_info(cwd: str) -> Optional[Dict[str, Any]]:
    """Return branch/dirty/ahead/behind info, or None outside a repo."""
    root = find_repo_root(cwd)
    if not root:
        return None

    now = time.time()
    cached = _CACHE.get(root)
    if cached and now - cached["_ts"] < _CACHE_TTL:
        return cached

    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    unborn = False
    if branch is None:
        # Fresh repo with no commits yet: HEAD is unborn, so rev-parse fails.
        # symbolic-ref still reports the branch name we are about to create.
        branch = _run(["git", "symbolic-ref", "--short", "HEAD"], root)
        unborn = True
        if branch is None:
            return None
    elif branch == "HEAD":
        short = _run(["git", "rev-parse", "--short", "HEAD"], root)
        branch = f"detached@{short}" if short else "detached"

    porcelain = _run(["git", "status", "--porcelain"], root, timeout=1.0)
    staged = unstaged = untracked = 0
    if porcelain:
        for line in porcelain.splitlines():
            if len(line) < 2:
                continue
            x, y = line[0], line[1]
            if x == "?" and y == "?":
                untracked += 1
                continue
            if x != " ":
                staged += 1
            if y != " ":
                unstaged += 1

    ahead = behind = 0
    counts = _run(["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"], root)
    if counts:
        parts = counts.split()
        if len(parts) == 2:
            try:
                behind, ahead = int(parts[0]), int(parts[1])
            except ValueError:
                pass

    remote = _run(["git", "remote", "get-url", "origin"], root)

    info = {
        "root": root,
        "branch": branch,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "dirty": bool(staged or unstaged or untracked),
        "ahead": ahead,
        "behind": behind,
        "remote": remote,
        "has_remote": bool(remote),
        "unborn": unborn,
        "_ts": now,
    }
    _CACHE[root] = info
    return info


def invalidate_cache() -> None:
    """Drop cached git info (call after any git-mutating command)."""
    _CACHE.clear()


def current_branch(cwd: str) -> Optional[str]:
    info = get_git_info(cwd)
    return info["branch"] if info else None


def is_repo(cwd: str) -> bool:
    return find_repo_root(cwd) is not None
