from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

from matplotlib.colors import CSS4_COLORS

from formulas import backup_original
from i18n import t

CATALOG_NAME = "Functieoverzicht.def"
_RENAMED_BUILTINS = {"STAP": "STEP", "ZONOP": "SUNUP", "ZONONDER": "SUNDOWN"}
_NAMED_COLORS = {name.casefold() for name in CSS4_COLORS}


@dataclass
class FunctionEntry:
    name: str
    visible: bool = True


@dataclass
class FunctionGroup:
    name: str
    color: str
    functions: list[FunctionEntry] = field(default_factory=list)


def parse_catalog(content: str, source: str | Path = CATALOG_NAME) -> list[FunctionGroup]:
    try:
        data = json.loads(content)
        if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
            raise ValueError("groups")
        groups = []
        seen: set[str] = set()
        group_names: set[str] = set()
        for item in data["groups"]:
            if not isinstance(item, dict):
                raise ValueError("group")
            name, color, entries = item.get("name"), item.get("color"), item.get("functions")
            if (not isinstance(name, str) or not name.strip() or name.casefold() in group_names
                    or not isinstance(color, str)
                    or not (re.fullmatch(r"#[0-9a-fA-F]{6}", color) or color.casefold() in _NAMED_COLORS)
                    or not isinstance(entries, list)):
                raise ValueError("group")
            group_names.add(name.casefold())
            functions = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("function")
                function_name, visible = entry.get("name"), entry.get("visible")
                if (not isinstance(function_name, str) or not re.fullmatch(r"[A-Za-z_]\w*", function_name)
                        or function_name.upper() in seen or not isinstance(visible, bool)):
                    raise ValueError("function")
                seen.add(function_name.upper())
                functions.append(FunctionEntry(function_name, visible))
            groups.append(FunctionGroup(name, color, functions))
        return groups
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(t("formula.catalog_invalid", path=source, error=exc)) from exc


def load_catalog(path: Path) -> list[FunctionGroup]:
    if not path.exists():
        return []
    return parse_catalog(path.read_text(encoding="utf-8"), path)


def include_unlisted(groups: list[FunctionGroup], names: list[str]) -> None:
    listed = {entry.name.upper() for group in groups for entry in group.functions}
    missing = [name for name in names if name.upper() not in listed]
    if missing:
        group = next((group for group in groups if group.name == t("formula.catalog_other")), None)
        if group is None:
            group = FunctionGroup(t("formula.catalog_other"), "#555555")
            groups.append(group)
        group.functions.extend(FunctionEntry(name) for name in missing)


def sync_catalog(groups: list[FunctionGroup], builtins: list[str], macros: dict[str, dict[str, Any]]) -> None:
    available = {name.upper(): name for name in builtins}
    available.update({name.upper(): macro["name"] for name, macro in macros.items()})
    listed = {entry.name.upper() for group in groups for entry in group.functions}
    for group in groups:
        for entry in group.functions[:]:
            replacement = _RENAMED_BUILTINS.get(entry.name.upper())
            if replacement and replacement in available:
                if replacement in listed:
                    group.functions.remove(entry)
                else:
                    entry.name = available[replacement]
                    listed.add(replacement)
    existing = {entry.name.upper(): (group, entry) for group in groups for entry in group.functions}
    for group in groups:
        group.functions[:] = [entry for entry in group.functions if entry.name.upper() in available]
    for key, name in available.items():
        previous = existing.get(key)
        desired = macros[key].get("group") if key in macros else None
        target = next((group for group in groups if desired and group.name.casefold() == desired.casefold()), None)
        if desired and target is None:
            target = FunctionGroup(desired, "#555555")
            groups.append(target)
        if target is None and previous:
            target = previous[0]
        if target is None:
            other = t("formula.catalog_other")
            target = next((group for group in groups if group.name.casefold() == other.casefold()), None)
            if target is None:
                target = FunctionGroup(other, "#555555")
                groups.append(target)
        if previous:
            old_group, entry = previous
            entry.name = name
            if old_group is not target:
                old_group.functions.remove(entry)
                target.functions.append(entry)
        else:
            target.functions.append(FunctionEntry(name))


def serialize_catalog(groups: list[FunctionGroup]) -> str:
    content = {"groups": [
        {"name": group.name, "color": group.color, "functions": [
            {"name": entry.name, "visible": entry.visible} for entry in group.functions
        ]} for group in groups
    ]}
    return json.dumps(content, ensure_ascii=False, indent=2) + "\n"


def save_catalog(path: Path, groups: list[FunctionGroup]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_original(path)
    path.write_text(serialize_catalog(groups), encoding="utf-8")
