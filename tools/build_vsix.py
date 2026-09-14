'''
Build the GBit Shell VS Code extension package (.vsix) with no network
access and no Node tooling.

A .vsix is just a ZIP archive containing:
    extension.vsixmanifest    metadata read by VS Code
    [Content_Types].xml       MIME map for the file extensions inside
    extension/...             the extension files themselves

Usage:
    python tools/build_vsix.py [output_dir]
'''
import json
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "vscode-extension"

# Files shipped inside the package, in order
INCLUDE = ("package.json", "extension.js", "README.md", "LICENSE")

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


def build_manifest(pkg: dict) -> str:
    """Render extension.vsixmanifest from package.json."""
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
            f'Value="{escape(source)}"/>\n'
            f'      <Property Id="Microsoft.VisualStudio.Services.Links.Getstarted" '
            f'Value="{escape(source)}"/>\n'
            f'      <Property Id="Microsoft.VisualStudio.Services.Links.GitHub" '
            f'Value="{escape(source)}"/>\n'
        )

    return f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0"
    xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011"
    xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{escape(identity)}" Version="{escape(version)}"
              Publisher="{escape(publisher)}"/>
    <DisplayName>{escape(display)}</DisplayName>
    <Description xml:space="preserve">{escape(description)}</Description>
    <Tags>{escape(tags)}</Tags>
    <Categories>{escape(categories)}</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{escape(engine)}"/>
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


def main(argv) -> int:
    package_file = EXT_DIR / "package.json"
    if not package_file.is_file():
        print(f"nao encontrei {package_file}", file=sys.stderr)
        return 1
    pkg = json.loads(package_file.read_text(encoding="utf-8"))

    out_dir = Path(argv[0]).resolve() if argv else ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{pkg['publisher']}.{pkg['name']}-{pkg['version']}.vsix"

    missing = [name for name in INCLUDE if not (EXT_DIR / name).is_file()]
    if missing:
        print(f"faltando: {', '.join(missing)}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("extension.vsixmanifest", build_manifest(pkg))
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        for name in INCLUDE:
            archive.write(EXT_DIR / name, f"extension/{name}")

    size = target.stat().st_size / 1024
    print(f"{target.name} criado ({size:.1f} KB)")
    print("instale com:  code --install-extension " + target.name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
