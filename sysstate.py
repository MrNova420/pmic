"""Transactional journal of system-level changes PMic makes.

Rule: anything PMic changes about the machine (currently: the Windows default
recording device) is written down here — before value, after value, time —
BEFORE the change is applied. Then:

  * clean quit      -> revert_all() restores every journaled change
  * crash / kill    -> nothing runs at crash time, but the journal survives,
                       and the NEXT launch calls revert_all() before doing
                       anything else, so the system is left clean

Nothing PMic does to the system is ever a one-way door.
"""
from __future__ import annotations

import json
import os
import time

_NAME = "pmic-sysstate.json"


def journal_path(config_path: str) -> str:
    """Journal lives next to the config file (same dir as pmic-config.json)."""
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), _NAME)


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path: str, state: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------- default mic

def record_default_mic(config_path: str, before_id: str | None, after_id: str | None) -> None:
    """Journal a default-mic change. No-op when nothing actually changed."""
    if not before_id or not after_id or before_id == after_id:
        return
    path = journal_path(config_path)
    state = _load(path)
    # keep the ORIGINAL before value if one is already journaled — reverting
    # must always go back to what the system looked like before PMic started
    prev = state.get("default_mic", {})
    state["default_mic"] = {
        "before": prev.get("before") or before_id,
        "after": after_id,
        "ts": time.time(),
    }
    _save(path, state)


def clear_default_mic(config_path: str) -> None:
    path = journal_path(config_path)
    state = _load(path)
    if state.pop("default_mic", None) is not None:
        _save(path, state)


def has_default_mic_entry(config_path: str) -> bool:
    return "default_mic" in _load(journal_path(config_path))


def revert_all(config_path: str) -> dict:
    """Undo every journaled system change. Safe to call any time.

    Returns {"default_mic": "reverted"|"no_change"|"no_previous"|...}
    """
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    import default_mic as dmic  # noqa: local import keeps module importable off-Windows
    path = journal_path(config_path)
    state = _load(path)
    result: dict = {}

    entry = state.get("default_mic")
    if entry:
        before = entry.get("before")
        current = dmic.get_default_capture_id()
        if before and current != before:
            ok = dmic.set_default_capture(before)
            result["default_mic"] = "reverted" if ok else "revert_failed"
        else:
            result["default_mic"] = "no_change"
        # consumed either way — the journal describes "PMic changed this",
        # and after revert_all it no longer does
        clear_default_mic(config_path)
    else:
        result["default_mic"] = "no_entry"
    return result
