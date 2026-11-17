from __future__ import annotations

import re

import numpy as np
import pandas as pd
from scipy import stats

from i18n import t

TIME_VARIABLES = ("time_days", "hour", "doy")


def sanitize(name: str) -> str:
    key = re.sub(r"\W+", "_", str(name)).strip("_")
    if not key or key[0].isdigit():
        key = f"v_{key}"
    return key


def numeric_frame(data: pd.DataFrame, header_rows: int) -> pd.DataFrame:
    """Numerieke meetreeksen met tijdindex; kolomnamen zijn formule-vriendelijk (zie attrs['names'])."""
    body = data.iloc[header_rows:]
    times = pd.to_datetime(body.iloc[:, 0], format="mixed", errors="coerce")
    valid = times.notna().to_numpy()
    if valid.sum() < 2:
        raise ValueError(t("addon.modelling.no_valid_timestamps_in_the_first"))
    columns: dict[str, np.ndarray] = {}
    names: dict[str, str] = {}
    for column in body.columns[1:]:
        values = pd.to_numeric(body[column], errors="coerce")
        if not values.notna().any():
            continue
        key = base = sanitize(column)
        suffix = 2
        while key in columns:
            key = f"{base}_{suffix}"
            suffix += 1
        columns[key] = values.to_numpy(float)[valid]
        names[key] = str(column)
    if not columns:
        raise ValueError(t("addon.modelling.no_numeric_columns_found"))
    frame = pd.DataFrame(columns, index=pd.DatetimeIndex(times[valid].to_numpy()))
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    seconds = (frame.index - frame.index[0]).total_seconds().to_numpy()
    extra = {
        "time_days": seconds / 86400.0,
        "hour": frame.index.hour.to_numpy() + frame.index.minute.to_numpy() / 60.0,
        "doy": frame.index.day_of_year.to_numpy().astype(float),
    }
    for key, values in extra.items():
        if key not in frame.columns:
            frame[key] = values
            names[key] = key
    frame.attrs["names"] = names
    return frame


def select_range(frame: pd.DataFrame, start: str = "", end: str = "") -> pd.DataFrame:
    try:
        lower = pd.Timestamp(start) if start.strip() else None
        upper = pd.Timestamp(end) if end.strip() else None
    except (ValueError, TypeError) as exc:
        raise ValueError(t("addon.modelling.invalid_start_or_end_date")) from exc
    result = frame
    if lower is not None:
        result = result[result.index >= lower]
    if upper is not None:
        result = result[result.index <= upper]
    if result.empty:
        raise ValueError(t("addon.modelling.no_data_in_the_selected_period"))
    result.attrs.update(frame.attrs)
    return result


def split_chronological(frame: pd.DataFrame, train_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    count = max(1, int(len(frame) * min(max(train_fraction, 0.05), 1.0)))
    return frame.iloc[:count], frame.iloc[count:]


def describe(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    subset = frame[columns]
    table = subset.describe().T
    table["missing"] = subset.isna().sum()
    table["skew"] = subset.skew()
    table["kurtosis"] = subset.kurt()
    return table


def correlation(frame: pd.DataFrame, columns: list[str], method: str = "pearson") -> pd.DataFrame:
    return frame[columns].corr(method=method)


def pair_statistics(x: pd.Series, y: pd.Series) -> dict[str, float]:
    pair = pd.concat([x, y], axis=1).dropna()
    if len(pair) < 3:
        raise ValueError(t("addon.modelling.too_few_overlapping_values"))
    pearson = stats.pearsonr(pair.iloc[:, 0], pair.iloc[:, 1])
    spearman = stats.spearmanr(pair.iloc[:, 0], pair.iloc[:, 1])
    line = stats.linregress(pair.iloc[:, 0], pair.iloc[:, 1])
    return {
        "n": float(len(pair)), "pearson_r": float(pearson[0]), "pearson_p": float(pearson[1]),
        "spearman_rho": float(spearman[0]), "spearman_p": float(spearman[1]),
        "slope": float(line.slope), "intercept": float(line.intercept),
    }


def normality(series: pd.Series) -> tuple[float, float] | None:
    values = series.dropna().to_numpy(float)
    if len(values) < 8 or np.ptp(values) == 0:
        return None
    if len(values) > 5000:
        values = values[np.linspace(0, len(values) - 1, 5000).astype(int)]
    result = stats.shapiro(values)
    return float(result.statistic), float(result.pvalue)
