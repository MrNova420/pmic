# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PMic — run: python -m PyInstaller PMic.spec
import sys
from PyInstaller.utils.hooks import collect_submodules

hidden = (collect_submodules("sounddevice") + collect_submodules("pystray")
          + collect_submodules("comtypes") + collect_submodules("webview"))
a = Analysis(
    ["server.py", "default_mic.py"],
    pathex=[],
    binaries=[],
    datas=[("web", "web"), ("vendor/VBCABLE_Driver_Pack46.zip", "vendor")],
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
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PMic",
)
