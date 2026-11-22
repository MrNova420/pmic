#!/usr/bin/env python3
"""Verify a built PMic bundle actually contains what the app needs at runtime.

A PyInstaller build can succeed while still shipping a broken app — a missing
web/ folder or driver pack only shows up when a user clicks "Install virtual
mic". This checks the built tree, so a release never goes out half-assembled.

Run: python tools/check_bundle.py dist/PMic [zip]
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REQUIRED_FILES = [
    "web/index.html",
    "web/panel.html",
    "web/client.js",
    "vendor/VBCABLE_Driver_Pack46.zip",
    "assets/pmic.ico",
]


def find(root: Path, rel: str) -> Path | None:
    """Locate a bundled file. PyInstaller onedir puts data either at the top
    level (older) or under _internal/ (newer), so accept both."""
    for cand in (root / "_internal" / rel, root / rel):
        if cand.exists():
            return cand
    return None


def check_dir(root: Path) -> list[str]:
    problems = []
    exe = root / "PMic.exe"
    if not exe.exists():
        problems.append("missing: PMic.exe")
    elif exe.stat().st_size < 500_000:
        problems.append(f"PMic.exe looks too small ({exe.stat().st_size} bytes)")
    for rel in REQUIRED_FILES:
        if find(root, rel) is None:
            problems.append(f"missing: {rel}")
    driver = find(root, "vendor/VBCABLE_Driver_Pack46.zip")
    if driver is not None and driver.stat().st_size < 1_000_000:
        problems.append(f"driver pack looks truncated ({driver.stat().st_size} bytes)")
    return problems


def check_zip(path: Path) -> list[str]:
    problems = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "PMic.exe" not in names:
            problems.append("missing from zip: PMic.exe")
        for rel in REQUIRED_FILES:
            if rel not in names and f"_internal/{rel}" not in names:
                problems.append(f"missing from zip: {rel}")
    return problems


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    problems = check_dir(root) if root.is_dir() else check_zip(root)
    if problems:
        print("bundle check FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.is_dir() else path_size(root)
    print(f"bundle check ok: {root} ({total / 1_048_576:.1f} MiB)")
    return 0


def path_size(p: Path) -> int:
    return p.stat().st_size


if __name__ == "__main__":
    raise SystemExit(main())
