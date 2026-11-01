"""Vertalingen uit Configs/Langdefs/<taalcode>.langdef (regel: sleutel=tekst, \\n = nieuwe regel)."""
from __future__ import annotations

from pathlib import Path
import sys

from settings import get_language

_APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
LANGDEF_DIR = _APP_DIR / "Configs" / "Langdefs"
DEFAULT_LANGUAGE = "nl"

_current = DEFAULT_LANGUAGE
_tables: dict[str, dict[str, str]] = {}


def _load_table(code: str) -> dict[str, str]:
    if code not in _tables:
        table: dict[str, str] = {}
        try:
            text = (LANGDEF_DIR / f"{code}.langdef").read_text(encoding="utf-8")
        except OSError:
            return table
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if separator:
                table[key.strip()] = value.strip().replace("\\n", "\n")
        _tables[code] = table
    return _tables[code]


def available_languages() -> dict[str, str]:
    """Taalcode -> eigen taalnaam, uit alle *.langdef-bestanden."""
    languages: dict[str, str] = {}
    if LANGDEF_DIR.is_dir():
        for path in sorted(LANGDEF_DIR.glob("*.langdef")):
            languages[path.stem] = _load_table(path.stem).get("language.name", path.stem)
    return languages or {DEFAULT_LANGUAGE: "Nederlands"}


def set_language(code: str) -> None:
    global _current
    _current = code if code in available_languages() else DEFAULT_LANGUAGE


def current_language() -> str:
    return _current


def init_language() -> None:
    set_language(get_language())


def t(key: str, default: str | None = None, **values: object) -> str:
    """Tekst in de gekozen taal; valt terug op Nederlands, dan op default of de sleutel."""
    text = _load_table(_current).get(key)
    if text is None:
        text = _load_table(DEFAULT_LANGUAGE).get(key)
    if text is None:
        text = default if default is not None else key
    if values:
        try:
            return text.format(**values)
        except (KeyError, IndexError, ValueError):
            return text
    return text
