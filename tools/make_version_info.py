#!/usr/bin/env python3
"""Generate the Windows version resource consumed by PyInstaller (PMic.spec).

Keeps ProductName/FileVersion/CompanyName in one place so that Properties →
Details on PMic.exe, Add/Remove Programs and the release tag never disagree.

Run: python tools/make_version_info.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "version_info.txt"

PUBLISHER = "MrNova420"
PRODUCT = "PMic"
DESCRIPTION = "Use your phone as a microphone for your Windows PC"

version = re.search(
    r'^VERSION\s*=\s*"([^"]+)"',
    (ROOT / "server.py").read_text(encoding="utf-8"),
    re.M,
).group(1)

numeric = [int(p) for p in re.findall(r"\d+", version.split("-")[0])]
numeric = (numeric + [0, 0, 0])[:4]
tuple_ = ", ".join(str(n) for n in numeric)

template = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({tuple_}),
    prodvers=({tuple_}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '{PUBLISHER}'),
        StringStruct('FileDescription', '{DESCRIPTION}'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'PMic'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 {PUBLISHER}. MIT licensed.'),
        StringStruct('OriginalFilename', 'PMic.exe'),
        StringStruct('ProductName', '{PRODUCT}'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(template, encoding="utf-8")
# also expose the plain version so the .bat build scripts never have to parse python
(OUT.parent / "version.txt").write_text(version, encoding="utf-8")
print(f"wrote {OUT} (version {version}, numeric {'.'.join(map(str, numeric))})")
