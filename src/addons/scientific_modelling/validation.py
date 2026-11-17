from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from i18n import t


def paired(observed: pd.Series, predicted: pd.Series) -> pd.DataFrame:
    pair = pd.concat([observed.rename("observed"), predicted.rename("predicted")], axis=1).dropna()
    if len(pair) < 3:
        raise ValueError(t("addon.modelling.too_few_overlapping_values_between_measurement"))
    pair["residual"] = pair["observed"] - pair["predicted"]
    return pair


def metrics(pair: pd.DataFrame) -> dict[str, float]:
    obs, pred, res = (pair[c].to_numpy(float) for c in ("observed", "predicted", "residual"))
    total = float(((obs - obs.mean()) ** 2).sum())
    r = float(np.corrcoef(obs, pred)[0, 1]) if obs.std() > 0 and pred.std() > 0 else float("nan")
    return {
        "n": float(len(pair)),
        "R2": 1 - float((res ** 2).sum()) / total if total > 0 else float("nan"),
        "r": r,
        "RMSE": float(np.sqrt((res ** 2).mean())),
        "MAE": float(np.abs(res).mean()),
        "Bias": float((pred - obs).mean()),
    }


def residual_analysis(pair: pd.DataFrame) -> dict[str, float]:
    res = pair["residual"].to_numpy(float)
    denominator = float((res ** 2).sum())
    result = {
        "mean": float(res.mean()), "std": float(res.std(ddof=1)), "skew": float(stats.skew(res)),
        "durbin_watson": float((np.diff(res) ** 2).sum() / denominator) if denominator > 0 else float("nan"),
        "lag1_autocorrelation": float(np.corrcoef(res[:-1], res[1:])[0, 1]) if res.std() > 0 else float("nan"),
    }
    if len(res) >= 8 and res.std() > 0:
        sample = res if len(res) <= 5000 else res[np.linspace(0, len(res) - 1, 5000).astype(int)]
        result["shapiro_p"] = float(stats.shapiro(sample).pvalue)
    return result
