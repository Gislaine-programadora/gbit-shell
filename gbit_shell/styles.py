'''
GBit Shell - Color themes for prompt_toolkit
'''
from prompt_toolkit.styles import Style

# GBit brand palette (v1.0.0 – no neon)
#   blue   #0078d4   sky  #38bdf8   green #4ade80
#   amber  #fbbf24   red  #f87171   slate #94a3b8

THEMES = {
    "gbit": {
        "user": "#4ade80 bold",
        "at": "#64748b",
        "host": "#38bdf8",
        "tag": "bg:#0078d4 #ffffff bold",
        "path": "#60a5fa bold",
        "git.paren": "#64748b",
        "git.clean": "#4ade80",
        "git.dirty": "#fbbf24",
        "git.staged": "#4ade80",
        "git.unstaged": "#fb923c",
        "git.untracked": "#94a3b8",
        "git.ahead": "#38bdf8",
        "git.behind": "#f87171",
        "venv": "#3b82f6",
        "node": "#4ade80",
        "project": "#3b82f6 bold",
        "jobs": "#fbbf24",
        "time": "#64748b",
        "exitcode": "#f87171 bold",
        "arrow.ok": "#0078d4 bold",
        "arrow.err": "#f87171 bold",
        "arrow.cont": "#64748b",
        # completion menu
        "completion-menu.completion": "bg:#1e293b #cbd5e1",
        "completion-menu.completion.current": "bg:#0078d4 #ffffff bold",
        "completion-menu.meta.completion": "bg:#1e293b #64748b",
        "completion-menu.meta.completion.current": "bg:#005a9e #e2e8f0",
        "scrollbar.background": "bg:#1e293b",
        "scrollbar.button": "bg:#0078d4",
        # auto-suggestion (ghost text)
        "auto-suggestion": "#475569 italic",
        # search / bottom bar
        "bottom-toolbar": "bg:#0f172a #94a3b8",
        "bottom-toolbar.key": "bg:#0f172a #38bdf8 bold",
    },
    "mono": {
        "user": "bold",
        "at": "",
        "host": "",
        "tag": "reverse bold",
        "path": "bold",
        "git.paren": "",
        "git.clean": "",
        "git.dirty": "bold",
        "git.staged": "",
        "git.unstaged": "",
        "git.untracked": "",
        "git.ahead": "",
        "git.behind": "",
        "venv": "",
        "node": "",
        "project": "bold",
        "jobs": "",
        "time": "",
        "exitcode": "bold",
        "arrow.ok": "bold",
        "arrow.err": "bold reverse",
        "arrow.cont": "",
        "auto-suggestion": "italic",
    },
    "ocean": {
        "user": "#7dd3fc bold",
        "at": "#475569",
        "host": "#38bdf8",
        "tag": "bg:#0284c7 #ffffff bold",
        "path": "#38bdf8 bold",
        "git.paren": "#475569",
        "git.clean": "#34d399",
        "git.dirty": "#facc15",
        "git.staged": "#34d399",
        "git.unstaged": "#fb923c",
        "git.untracked": "#94a3b8",
        "git.ahead": "#38bdf8",
        "git.behind": "#f87171",
        "venv": "#c4b5fd",
        "node": "#34d399",
        "project": "#c4b5fd bold",
        "jobs": "#facc15",
        "time": "#475569",
        "exitcode": "#f87171 bold",
        "arrow.ok": "#38bdf8 bold",
        "arrow.err": "#f87171 bold",
        "arrow.cont": "#475569",
        "auto-suggestion": "#334155 italic",
        "completion-menu.completion": "bg:#0c4a6e #e0f2fe",
        "completion-menu.completion.current": "bg:#0284c7 #ffffff bold",
    },
}


def get_style(theme_name: str = "gbit") -> Style:
    """Build a prompt_toolkit Style from a named theme."""
    rules = THEMES.get(theme_name, THEMES["gbit"])
    return Style.from_dict(rules)


def theme_names():
    return sorted(THEMES.keys())
