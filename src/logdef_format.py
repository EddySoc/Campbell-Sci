from __future__ import annotations

from i18n import t


def parse_chart_fields(line: str, name: str, number: int) -> list[str]:
    fields = [field.strip() for field in line.split(";")]
    if len(fields) not in (7, 8):
        raise ValueError(t("err.invalid_logdef_line", name=name, line=number))
    if len(fields) == 8 and (fields[7] != "linear" or fields[1].lower() != "scatter"):
        raise ValueError(t("err.invalid_logdef_line", name=name, line=number))
    return fields
