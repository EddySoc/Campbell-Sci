"""Persistente instellingen voor de Campbell Sci viewer."""
from __future__ import annotations

import json
from pathlib import Path

_SETTINGS_FILE = Path.home() / ".campbell_sci_viewer" / "settings.json"


def _load() -> dict:
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _update(key: str, value: object) -> None:
    data = _load()
    data[key] = value
    try:
        _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_FILE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def get_last_dir() -> str | None:
    last_dir = _load().get("last_dir")
    return last_dir if last_dir and Path(last_dir).is_dir() else None


def set_last_dir(directory: str | Path) -> None:
    _update("last_dir", str(directory))


def get_auto_scale_y() -> bool:
    return bool(_load().get("auto_scale_y", False))


def set_auto_scale_y(enabled: bool) -> None:
    _update("auto_scale_y", bool(enabled))
