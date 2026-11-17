from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from i18n import t


def regular_series(series: pd.Series) -> tuple[pd.Series, pd.Timedelta]:
    values = series.dropna().sort_index()
    values = values[~values.index.duplicated(keep="last")]
    if len(values) < 3:
        raise ValueError(t("addon.modelling.too_few_values_for_time_series"))
    step = values.index.to_series().diff().median()
    if not step or step <= pd.Timedelta(0):
        raise ValueError(t("addon.modelling.could_not_determine_the_time_step"))
    return values.resample(step).mean(), step


def trend(series: pd.Series) -> dict[str, float]:
    values = series.dropna()
    if len(values) < 3:
        raise ValueError(t("addon.modelling.too_few_values_for_a_trend"))
    days = ((values.index - values.index[0]).total_seconds() / 86400.0).to_numpy()
    observed = values.to_numpy(float)
    line = stats.linregress(days, observed)
    sample = np.linspace(0, len(days) - 1, min(len(days), 20000)).astype(int)
    tau = stats.kendalltau(days[sample], observed[sample])
    return {
        "slope_per_day": float(line.slope), "slope_per_year": float(line.slope * 365.25),
        "intercept": float(line.intercept), "r2": float(line.rvalue ** 2), "p_value": float(line.pvalue),
        "kendall_tau": float(tau[0]), "kendall_p": float(tau[1]),
    }


def moving_average(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(max(window, 1), center=True, min_periods=1).mean()


def decompose(series: pd.Series, period: str) -> pd.DataFrame:
    """Additieve decompositie in trend, seizoen (dag- of jaarcyclus) en rest."""
    full, step = regular_series(series)
    days = 1.0 if period == "day" else 365.25
    samples = int(round(pd.Timedelta(days=days) / step))
    if samples < 2 or len(full) < 2 * samples:
        raise ValueError(t("addon.modelling.the_series_is_too_short_or"))
    trend_part = full.rolling(samples, center=True, min_periods=max(samples // 2, 1)).mean()
    phase = np.arange(len(full)) % samples
    profile = (full - trend_part).groupby(phase).mean()
    profile -= profile.mean()
    seasonal = pd.Series(profile.reindex(phase).to_numpy(), index=full.index)
    return pd.DataFrame({
        "observed": full, "trend": trend_part, "seasonal": seasonal, "residual": full - trend_part - seasonal,
    })


def autocorrelation(series: pd.Series, max_lag: int) -> pd.Series:
    full, _step = regular_series(series)
    full = full.interpolate(limit=3)
    lags = range(1, max(min(max_lag, len(full) // 3), 2) + 1)
    return pd.Series([full.autocorr(lag) for lag in lags], index=list(lags), name="autocorrelation")


def diurnal_profile(series: pd.Series) -> pd.DataFrame:
    grouped = series.dropna().groupby(series.dropna().index.hour)
    return pd.DataFrame({"mean": grouped.mean(), "std": grouped.std()})
