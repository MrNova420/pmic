#!/usr/bin/env python3
"""Write SHA256SUMS.txt for the built release artifacts.

Unsigned binaries are a fact of life for a hobby project, so the least we can
do is let people verify that what they downloaded is what we built.

Run: python tools/make_checksums.py PMic-Setup-1.4.1-x64.msi PMic-1.4.1-portable-win64.zip
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "SHA256SUMS.txt"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    lines = []
    missing = []
    for name in argv:
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        if not path.exists():
            missing.append(name)
            continue
        digest = sha256(path)
        lines.append(f"{digest}  {path.name}")
        print(f"{digest}  {path.name}  ({path.stat().st_size / 1_048_576:.1f} MiB)")
    if missing:
        print("missing artifacts:", ", ".join(missing), file=sys.stderr)
        return 1
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT.name} ({len(lines)} artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
