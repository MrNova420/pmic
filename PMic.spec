# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PMic — run: python -m PyInstaller PMic.spec
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# single source of truth for the version lives in server.py
_ver_src = Path("server.py").read_text(encoding="utf-8")
VERSION = re.search(r'^VERSION\s*=\s*"([^"]+)"', _ver_src, re.M).group(1)
# Windows file/version resources and MSI ProductVersion accept digits and dots
NUMERIC = ".".join((VERSION.split("-")[0].split(".") + ["0", "0", "0"])[:4])

_ver_file = Path("build/version_info.txt")
ICON = "assets/pmic.ico"

hidden = (collect_submodules("sounddevice") + collect_submodules("pystray")
          + collect_submodules("comtypes") + collect_submodules("webview"))
a = Analysis(
    ["server.py", "default_mic.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("web", "web"),
        ("assets/pmic.ico", "assets"),
        ("vendor/VBCABLE_Driver_Pack46.zip", "vendor"),
    ],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "pytest", "setuptools"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PMic",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # tray app: no console window
    icon=ICON if Path(ICON).exists() else None,
    version=str(_ver_file) if _ver_file.exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PMic",
)
