from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from formulas import backup_original, formula_candidates
from i18n import t

PERIODS = ("second15", "second30", "minute", "minute5", "minute10", "minute15", "minute30", "hour", "day", "week", "month", "year")
MINUTE_FREQUENCIES = {
    "second15": "15s", "second30": "30s", "minute": "1min", "minute5": "5min",
    "minute10": "10min", "minute15": "15min", "minute30": "30min",
}
AGGREGATIONS = ("mean", "sum", "min", "max", "count", "median")


def pivot_path(logger: str, location: str | None, directory: Path) -> Path:
    candidates = [p.with_suffix(".pivotdef") for p in formula_candidates(logger, location, directory)]
    return next((p for p in candidates if p.is_file()), candidates[0])


def validate_pivots(definitions: Any) -> list[dict[str, str]]:
    if not isinstance(definitions, list):
        raise ValueError(t("pivot.invalid_definition"))
    result: list[dict[str, str]] = []
    names: set[str] = set()
    for item in definitions:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) for k in ("name", "column", "period", "aggregation")):
            raise ValueError(t("pivot.invalid_definition"))
        row = {k: item[k].strip() for k in ("name", "column", "period", "aggregation")}
        if not row["name"] or not row["column"] or row["period"] not in PERIODS or row["aggregation"] not in AGGREGATIONS:
            raise ValueError(t("pivot.invalid_definition"))
        if row["name"].casefold() in names:
            raise ValueError(t("pivot.duplicate", name=row["name"]))
        names.add(row["name"].casefold())
        result.append(row)
    return result


def load_pivots(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        return validate_pivots(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name}: {exc}") from exc


def save_pivots(path: Path, definitions: list[dict[str, str]]) -> None:
    definitions = validate_pivots(definitions)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_original(path)
    path.write_text(json.dumps(definitions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def period_start(times: pd.Series, period: str) -> pd.Series:
    if period in MINUTE_FREQUENCIES:
        return times.dt.floor(MINUTE_FREQUENCIES[period])
    if period == "hour":
        return times.dt.floor("h")
    if period == "day":
        return times.dt.normalize()
    if period == "week":
        return times.dt.normalize() - pd.to_timedelta(times.dt.day_of_week, unit="D")
    if period == "month":
        return times.dt.to_period("M").dt.to_timestamp()
    return times.dt.to_period("Y").dt.to_timestamp()


def calculate_pivots(data: pd.DataFrame, header_rows: int, definitions: list[dict[str, str]]) -> dict[str, pd.DataFrame]:
    definitions = validate_pivots(definitions)
    if not definitions:
        return {}
    source = data.iloc[header_rows:]
    time_column = str(data.columns[0])
    times = pd.to_datetime(source.iloc[:, 0], format="mixed", errors="coerce")
    if not times.notna().any():
        raise ValueError(t("pivot.no_time"))
    frames: dict[str, pd.DataFrame] = {}
    for definition in definitions:
        name, column, period, aggregation = (definition[k] for k in ("name", "column", "period", "aggregation"))
        if column not in source.columns:
            raise ValueError(t("err.column_not_in_data", name=column))
        if name.casefold() in {str(c).casefold() for c in data.columns}:
            raise ValueError(t("pivot.duplicate", name=name))
        values = pd.to_numeric(source[column], errors="coerce")
        if not values.notna().any():
            raise ValueError(t("pivot.no_numeric", name=column))
        invalid_times = int((times.isna() & values.notna()).sum())
        if invalid_times:
            raise ValueError(t("pivot.invalid_times", name=column, count=invalid_times))
        stamps = period_start(times, period)
        grouped = values.groupby(pd.Series(stamps, index=source.index), sort=True)
        series = grouped.sum(min_count=1) if aggregation == "sum" else getattr(grouped, aggregation)()
        series.name = name
        if period in frames:
            frames[period] = frames[period].join(series, how="outer")
        else:
            frames[period] = series.to_frame()
    for period, frame in frames.items():
        frames[period] = frame.rename_axis(time_column).reset_index()
    return frames
