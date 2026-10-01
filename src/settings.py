"""Persistente instellingen voor de Campbell Sci viewer."""
from __future__ import annotations

import json
from pathlib import Path

_SETTINGS_FILE = Path.home() / ".campbell_sci_viewer" / "settings.json"


def get_last_dir() -> str | None:
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        last_dir = data.get("last_dir")
        return last_dir if last_dir and Path(last_dir).is_dir() else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def set_last_dir(directory: str | Path) -> None:
    try:
        _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_FILE.write_text(json.dumps({"last_dir": str(directory)}), encoding="utf-8")
    except OSError:
        pass
