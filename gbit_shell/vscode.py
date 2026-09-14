'''
GBit Shell - VS Code extension installer
Generates the .vsix from the bundled vscode-extension/ source files
and installs it with `code --install-extension`.

This module is self-contained: the extension source files are embedded
as Python dicts, so the .vsix can be built from any machine that has
GBit Shell installed — no git clone or dist/ folder needed.
'''
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from . import __version__


# ======================================================================
# Extension source files — embedded so `gbit vscode` works anywhere
# ======================================================================

PACKAGE_JSON = {
    "name": "gbit-shell-terminal",
    "displayName": "GBit Shell",
    "description": (
        "Adds GBit Shell as an integrated terminal profile in VS Code — "
        "modern prompt, git-aware, one-command GitHub publishing"
    ),
    "version": "1.0.0",
    "publisher": "gislaine",
    "license": "MIT",
    "engines": {"vscode": "^1.75.0"},
    "categories": ["Other", "SCM Providers"],
    "keywords": ["terminal", "shell", "git", "github", "prompt"],
    "main": "./extension.js",
    "activationEvents": [
        "onStartupFinished",
        "onTerminalProfile:gbit-shell.terminal-profile",
        "onCommand:gbit-shell.newTerminal",
        "onCommand:gbit-shell.splitTerminal",
        "onCommand:gbit-shell.publishToGitHub",
        "onCommand:gbit-shell.addProfile",
        "onCommand:gbit-shell.setDefault",
        "onCommand:gbit-shell.doctor",
    ],
    "contributes": {
        "terminal": {
            "profiles": [{
                "id": "gbit-shell.terminal-profile",
                "title": "GBit Shell",
                "icon": "terminal",
            }]
        },
        "commands": [
            {"command": "gbit-shell.newTerminal",
             "title": "GBit Shell: Novo terminal", "category": "GBit Shell"},
            {"command": "gbit-shell.splitTerminal",
             "title": "GBit Shell: Novo terminal ao lado", "category": "GBit Shell"},
            {"command": "gbit-shell.publishToGitHub",
             "title": "GBit Shell: Publicar no GitHub (ghpush)", "category": "GBit Shell"},
            {"command": "gbit-shell.addProfile",
             "title": "GBit Shell: Adicionar perfil ao settings.json", "category": "GBit Shell"},
            {"command": "gbit-shell.setDefault",
             "title": "GBit Shell: Definir como terminal padrao", "category": "GBit Shell"},
            {"command": "gbit-shell.doctor",
             "title": "GBit Shell: Verificar instalacao", "category": "GBit Shell"},
        ],
        "configuration": {
            "title": "GBit Shell",
            "properties": {
                "gbitShell.pythonPath": {
                    "type": "string", "default": "",
                    "description": (
                        "Caminho do Python usado para rodar o GBit Shell. "
                        "Vazio = detectar automaticamente."
                    ),
                },
                "gbitShell.theme": {
                    "type": "string", "enum": ["gbit", "ocean", "mono"],
                    "default": "gbit",
                    "description": "Tema de cores do prompt.",
                },
                "gbitShell.showBanner": {
                    "type": "boolean", "default": False,
                    "description": (
                        "Mostrar o logo do GBit ao abrir o terminal integrado. "
                        "Desligado por padrao: o terminal abre limpo, como o Git Bash."
                    ),
                },
                "gbitShell.promptTag": {
                    "type": "string", "default": "GBIT",
                    "description": "Texto do selo exibido no prompt.",
                },
                "gbitShell.showNodeBadge": {
                    "type": "boolean", "default": False,
                    "description": (
                        "Mostrar o selo 'node' em projetos com package.json. "
                        "Desligado por padrao para um prompt mais limpo."
                    ),
                },
                "gbitShell.showGitStatus": {
                    "type": "boolean", "default": True,
                    "description": "Mostrar o estado do git no prompt.",
                },
            },
        },
        "menus": {
            "scm/title": [{
                "command": "gbit-shell.publishToGitHub",
                "group": "navigation",
            }],
            "terminal/context": [{
                "command": "gbit-shell.splitTerminal",
                "group": "navigation",
            }],
        },
    },
    "repository": {
        "type": "git",
        "url": "https://github.com/Gislaine-programadora/gbit-shell",
    },
}


LICENSE_TEXT = """MIT License

Copyright (c) 2026 Gislaine

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


README_VSCODE = """# GBit Shell — Extensao para VS Code

Adiciona o GBit Shell como perfil de terminal integrado no VS Code.

## Recursos

- Perfil de terminal "GBit Shell" na lista de terminais
- Comando para publicar no GitHub direto do painel SCM
- Configuracoes de tema, selo e banner
- Verificacao de instalacao (doctor)

## Instalacao

Dentro do GBit Shell, rode:

```
vscode
```

Ou manualmente:

```
code --install-extension ~/.gbit-shell/gislaine.gbit-shell-terminal-1.0.0.vsix
```
"""


# ======================================================================
# .vsix builder (pure Python, no Node needed)
# ======================================================================

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="js" ContentType="application/javascript"/>
  <Default Extension="md" ContentType="text/markdown"/>
  <Default Extension="txt" ContentType="text/plain"/>
  <Default Extension="png" ContentType="image/png"/>
  <Default Extension="svg" ContentType="image/svg+xml"/>
  <Default Extension="vsixmanifest" ContentType="text/xml"/>
</Types>
"""


def _build_manifest(pkg: dict) -> str:
    identity = pkg["name"]
    version = pkg["version"]
    publisher = pkg.get("publisher", "local")
    display = pkg.get("displayName", identity)
    description = pkg.get("description", "")
    tags = ",".join(pkg.get("keywords", []))
    categories = ",".join(pkg.get("categories", ["Other"]))
    engine = pkg.get("engines", {}).get("vscode", "^1.75.0")
    repository = pkg.get("repository", {})
    source = repository.get("url", "") if isinstance(repository, dict) else ""
    source = source.replace("git+", "").replace(".git", "")

    links = ""
    if source:
        links = (
            f'      <Property Id="Microsoft.VisualStudio.Services.Links.Source" '
            f'Value="{xml_escape(source)}"/>\n'
            f'      <Property Id="Microsoft.VisualStudio.Services.Links.Getstarted" '
            f'Value="{xml_escape(source)}"/>\n'
            f'      <Property Id="Microsoft.VisualStudio.Services.Links.GitHub" '
            f'Value="{xml_escape(source)}"/>\n'
        )

    return f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0"
    xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011"
    xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{xml_escape(identity)}" Version="{xml_escape(version)}"
              Publisher="{xml_escape(publisher)}"/>
    <DisplayName>{xml_escape(display)}</DisplayName>
    <Description xml:space="preserve">{xml_escape(description)}</Description>
    <Tags>{xml_escape(tags)}</Tags>
    <Categories>{xml_escape(categories)}</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{xml_escape(engine)}"/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="ui,workspace"/>
{links}    </Properties>
    <License>extension/LICENSE</License>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest"
           Path="extension/package.json" Addressable="true"/>
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details"
           Path="extension/README.md" Addressable="true"/>
    <Asset Type="Microsoft.VisualStudio.Services.Content.License"
           Path="extension/LICENSE" Addressable="true"/>
  </Assets>
</PackageManifest>
"""


def _read_extension_js() -> str:
    """Read extension.js from the installed gbit_shell package."""
    this_dir = Path(__file__).resolve().parent
    # Try sibling vscode-extension/ (source checkout / git clone)
    ext_file = this_dir.parent / "vscode-extension" / "extension.js"
    if ext_file.is_file():
        return ext_file.read_text(encoding="utf-8")
    # Fallback: try to find it next to the package
    for candidate in [
        this_dir / "_vscode_ext" / "extension.js",
        this_dir.parent / "vscode-extension" / "extension.js",
    ]:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return ""


def build_vsix(output_dir: Path) -> Path:
    """Build the .vsix and return its path."""
    pkg = PACKAGE_JSON
    target = output_dir / f"{pkg['publisher']}.{pkg['name']}-{pkg['version']}.vsix"

    ext_js = _read_extension_js()
    if not ext_js:
        raise FileNotFoundError(
            "extensao JS nao encontrada — o pacote gbit-shell foi instalado "
            "sem os fontes da extensao VS Code"
        )

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("extension.vsixmanifest", _build_manifest(pkg))
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("extension/package.json",
                         json.dumps(pkg, indent=2, ensure_ascii=False) + "\n")
        archive.writestr("extension/extension.js", ext_js)
        archive.writestr("extension/README.md", README_VSCODE)
        archive.writestr("extension/LICENSE", LICENSE_TEXT)

    return target


# ======================================================================
# Public command
# ======================================================================

def cmd_vscode(args, shell) -> int:
    """Install the GBit Shell extension into VS Code.

    Usage:
        vscode           build .vsix and install into VS Code
        vscode --build   only build the .vsix, don't install
    """
    from rich.console import Console
    from rich.markup import escape

    console = Console(highlight=False, soft_wrap=True)

    only_build = "--build" in args

    # 1. Find or build the .vsix
    # First check: is there already a .vsix in ~/.gbit-shell/ ?
    gbit_dir = Path.home() / ".gbit-shell"
    gbit_dir.mkdir(parents=True, exist_ok=True)

    # Check if extension.js is available
    ext_js = _read_extension_js()
    if not ext_js:
        console.print(
            "[red]VS Code extension source not found.[/]\n"
            "The gbit-shell package was installed without the extension sources.\n"
            "Install from the git repo instead:\n"
            "  [cyan]pip install git+https://github.com/Gislaine-programadora/gbit-shell.git[/]"
        )
        return 1

    console.print("[bold cyan][GBit][/] Construindo extensao VS Code...")

    try:
        vsix_path = build_vsix(gbit_dir)
    except Exception as exc:
        console.print(f"[red]erro ao gerar .vsix:[/] {exc}")
        return 1

    size_kb = vsix_path.stat().st_size / 1024
    console.print(f"  [green]+[/] {vsix_path.name} ({size_kb:.1f} KB)")

    if only_build:
        console.print(f"  [dim]salvo em: {vsix_path}[/]")
        console.print("  [dim]instale manualmente com:[/]")
        console.print(f"  [cyan]code --install-extension {vsix_path}[/]")
        return 0

    # 2. Install into VS Code
    code_cmd = shutil.which("code")
    if not code_cmd:
        # On Windows, check common paths
        if os.name == "nt":
            for candidate in [
                os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft VS Code\bin\code.cmd"),
            ]:
                if os.path.isfile(candidate):
                    code_cmd = candidate
                    break
        # macOS
        if not code_cmd and sys.platform == "darwin":
            app = "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
            if os.path.isfile(app):
                code_cmd = app

    if not code_cmd:
        console.print(
            "[yellow]comando 'code' nao encontrado.[/]\n"
            "  O VS Code precisa estar instalado e no PATH.\n"
            f"  Instale manualmente com:\n"
            f"  [cyan]code --install-extension {vsix_path}[/]"
        )
        return 1

    console.print("  [dim]instalando no VS Code...[/]")
    try:
        result = subprocess.run(
            [code_cmd, "--install-extension", str(vsix_path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            console.print("  [green]+[/] extensao instalada com sucesso!")
            console.print(
                "  [dim]recarregue a janela do VS Code:[/] "
                "[cyan]Ctrl+Shift+P[/] → [dim]Developer: Reload Window[/]"
            )
            console.print(
                "  [dim]depois abra o terminal e escolha[/] [bold]GBit Shell[/] "
                "[dim]na lista de perfis[/]"
            )
            return 0
        else:
            stderr = (result.stderr or "").strip()
            console.print(f"  [red]falha ao instalar:[/] {escape(stderr)}")
            console.print(
                f"  tente manualmente: [cyan]code --install-extension {vsix_path}[/]"
            )
            return 1
    except subprocess.TimeoutExpired:
        console.print("[red]timeout ao instalar a extensao[/]")
        return 1
    except OSError as exc:
        console.print(f"[red]erro:[/] {exc}")
        return 1
