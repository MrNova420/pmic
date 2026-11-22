#!/usr/bin/env python3
"""Generate the WiX source for the PMic MSI installer.

PyInstaller onedir builds have hundreds of files, and a hand-maintained
component list is how installers rot. This harvests a real `dist/PMic` tree
into deterministic WiX XML, then writes Package/Feature/shortcut plumbing
around it.

Run after "Build PMic.bat":
    python tools/make_installer.py
    wix build installer/PMic.wxs -b src=dist/PMic -o PMic-Setup.msi
"Build MSI.bat" does both.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dist" / "PMic"
OUT_DIR = ROOT / "installer"

PUBLISHER = "MrNova420"
PROJECT_URL = "https://github.com/MrNova420/pmic"
# Stable across every release: MajorUpgrade (upgrading one beta over another)
# and clean uninstall both depend on this GUID never changing. Do not edit.
UPGRADE_CODE = "8F3C2A16-4D7E-4B92-9C51-6E2B7A9D4F38"

version = re.search(
    r'^VERSION\s*=\s*"([^"]+)"',
    (ROOT / "server.py").read_text(encoding="utf-8"),
    re.M,
).group(1)
# MSI ProductVersion is numeric only: "1.4.1-beta.1" -> "1.4.1"
numeric = ".".join(re.findall(r"\d+", version.split("-")[0])[:3])
if numeric.count(".") != 2:
    sys.exit(f"cannot derive an MSI version from VERSION={version!r}")


def ident(text: str) -> str:
    """Windows Installer identifier: alphanumeric, '_' and '.' only."""
    cleaned = re.sub(r"[^0-9A-Za-z_.]", "_", text)
    if not cleaned or cleaned[0].isdigit():
        cleaned = "x_" + cleaned
    return cleaned


def short_id(prefix: str, text: str) -> str:
    """Build a unique, 72-char-safe identifier from a path.

    A hash of the exact path is always appended: sanitising alone is not
    injective ('Etc/GMT+9' and 'Etc/GMT-9' both become 'Etc_GMT_9'), and
    Windows Installer requires globally unique ids.
    """
    import hashlib

    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    base = prefix + ident(text)
    return f"{base[:57]}_{digest}"


def dir_id(rel: str) -> str:
    # rel is the raw path: ident() sanitises the readable part, the hash keeps
    # the id unique to this exact path.
    return "INSTALLFOLDER" if not rel else short_id("d_", rel)


def harvest() -> tuple[str, str, int]:
    """Return (directory tree xml, component group xml, file count)."""
    if not SRC.is_dir():
        sys.exit(f"{SRC} not found — run 'Build PMic.bat' first.")

    tree: dict = {}
    for path in sorted(SRC.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(SRC).parts
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = path

    dirs: list[str] = []
    comps: list[str] = []

    def walk(node: dict, rel: str, depth: int) -> None:
        pad = "  " * depth
        for name in sorted(k for k, v in node.items() if isinstance(v, dict)):
            sub = f"{rel}/{name}" if rel else name
            dirs.append(f'{pad}<Directory Id="{dir_id(sub)}" Name="{escape(name)}">')
            walk(node[name], sub, depth + 1)
            dirs.append(f"{pad}</Directory>")
        for name in sorted(k for k, v in node.items() if not isinstance(v, dict)):
            sub = f"{rel}/{name}" if rel else name
            src = "!(bindpath.src)" + sub.replace("/", "\\")
            comps.append(
                f'    <Component Id="{short_id("c_", sub)}" '
                f'Directory="{dir_id(rel)}" Guid="*">'
                f'<File Id="{short_id("f_", sub)}" '
                f'Source="{escape(src)}" />'
                f"</Component>"
            )

    walk(tree, "", 3)
    return "\n".join(dirs), "\n".join(comps), len(comps)


def text_to_rtf(text: str) -> str:
    """Wrap plain text as RTF for the MSI license page."""
    out = []
    for line in text.splitlines():
        buf = line.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        buf = "".join(
            ch if ord(ch) < 128 else f"\\u{ord(ch)}" + ("?" if ord(ch) < 32768 else "")
            for ch in buf
        )
        out.append("\\pard " + buf + "\\par")
    body = "\n".join(out)
    return (
        "{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0\\fswiss Consolas;}{\\f1\\fswiss Segoe UI;}}"
        "\\viewkind4\\uc1\\paperw12240\\paperh15840\\margl1200\\margr1200\\margt1000"
        "\\margb1000\\f1\\fs18\n" + body + "\n}"
    )


def main() -> None:
    dir_xml, comp_xml, n_files = harvest()
    OUT_DIR.mkdir(exist_ok=True)

    # the MSI license page must show the same text as LICENSE
    license_src = ROOT / "LICENSE"
    if not license_src.exists():
        sys.exit(f"{license_src} is missing — the installer's license page needs it.")
    (OUT_DIR / "LICENSE.rtf").write_text(
        text_to_rtf(license_src.read_text(encoding="utf-8")), encoding="utf-8"
    )

    # Included at the Wix root level by PMic.wxs. Root-level Wix accepts
    # Fragment elements only, so the harvested content lives inside one.
    (OUT_DIR / "Files.wxs").write_text(
        f"""<!-- GENERATED by tools/make_installer.py — do not edit by hand. -->
<Include xmlns="http://wixtoolset.org/schemas/v4/wxs">
  <Fragment>
    <DirectoryRef Id="INSTALLFOLDER">
{dir_xml}
    </DirectoryRef>

    <ComponentGroup Id="AppFiles">
{comp_xml}
    </ComponentGroup>
  </Fragment>
</Include>
""",
        encoding="utf-8",
    )

    license_rtf = (OUT_DIR / "LICENSE.rtf").resolve()

    (OUT_DIR / "PMic.wxs").write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<!-- GENERATED by tools/make_installer.py — do not edit by hand. -->
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs"
     xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui">
  <Package Name="PMic (Beta)"
           Manufacturer="{PUBLISHER}"
           Version="{numeric}"
           UpgradeCode="{UPGRADE_CODE}"
           Scope="perMachine"
           Language="1033"
           InstallerVersion="500">

    <SummaryInformation Description="PMic — use your phone as a microphone for your Windows PC (beta)"
                        Manufacturer="{PUBLISHER}" />

    <MajorUpgrade DowngradeErrorMessage="A newer version of PMic is already installed. Uninstall it first."
                  Schedule="afterInstallValidate" />
    <MediaTemplate EmbedCab="yes" />

    <!-- 64-bit Windows only: the bundled virtual-mic driver is x64 -->
    <Launch Condition="VersionNT64" Message="PMic requires 64-bit Windows." />
    <Launch Condition="VersionNT &gt;= 603" Message="PMic requires Windows 8.1 or newer." />

    <Icon Id="PMicIcon" SourceFile="{escape(str((ROOT / 'assets' / 'pmic.ico').resolve()))}" />
    <Property Id="ARPPRODUCTICON" Value="PMicIcon" />
    <Property Id="ARPHELPTELEMETRY" Value="no" />
    <Property Id="ARPCOMMENTS" Value="Beta build. Settings, pairing code and TLS certificate are stored per-user in %LOCALAPPDATA%\\PMic." />
    <Property Id="ARPURLINFOABOUT" Value="{PROJECT_URL}" />
    <Property Id="ARPURLUPDATEINFO" Value="{PROJECT_URL}/releases" />
    <Property Id="ARPCONTACT" Value="{PUBLISHER}" />

    <WixVariable Id="WixUILicenseRtf" Value="{license_rtf}" />
    <ui:WixUI Id="WixUI_Minimal" />

    <StandardDirectory Id="ProgramFiles64Folder">
      <Directory Id="INSTALLFOLDER" Name="PMic" />
    </StandardDirectory>
    <StandardDirectory Id="ProgramMenuFolder">
      <Directory Id="ProgramMenuDir" Name="PMic" />
    </StandardDirectory>
    <StandardDirectory Id="DesktopFolder" />

    <Feature Id="Main" Title="PMic" Level="1" AllowAbsent="no">
      <ComponentGroupRef Id="AppFiles" />
      <ComponentRef Id="StartMenuShortcut" />
      <ComponentRef Id="DesktopShortcutComponent" />
    </Feature>

    <Component Id="StartMenuShortcut" Directory="ProgramMenuDir" Guid="*">
      <Shortcut Id="StartMenuShortcutLink"
                Name="PMic"
                Description="Use your phone as a microphone"
                Target="[INSTALLFOLDER]PMic.exe"
                WorkingDirectory="INSTALLFOLDER" />
      <RemoveFolder Id="RemoveProgramMenuDir" On="uninstall" />
      <RegistryValue Root="HKLM" Key="Software\\{PUBLISHER}\\PMic"
                     Name="StartMenu" Type="integer" Value="1" KeyPath="yes" />
    </Component>

    <Component Id="DesktopShortcutComponent" Directory="DesktopFolder" Guid="*">
      <Shortcut Id="DesktopShortcutLink"
                Name="PMic"
                Description="Use your phone as a microphone"
                Target="[INSTALLFOLDER]PMic.exe"
                WorkingDirectory="INSTALLFOLDER" />
      <RegistryValue Root="HKLM" Key="Software\\{PUBLISHER}\\PMic"
                     Name="DesktopShortcut" Type="integer" Value="1" KeyPath="yes" />
    </Component>
  </Package>

  <?include Files.wxs?>
</Wix>
""",
        encoding="utf-8",
    )

    print(f"wrote {OUT_DIR / 'PMic.wxs'} (MSI version {numeric}, {n_files} files)")
    print(f"wrote {OUT_DIR / 'Files.wxs'}")


if __name__ == "__main__":
    main()
