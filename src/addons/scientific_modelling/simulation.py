from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from i18n import t

MODES = ("add", "mul", "set")
Scenario = dict[str, tuple[str, float]]


def apply_scenario(frame: pd.DataFrame, scenario: Scenario) -> pd.DataFrame:
    result = frame.copy()
    for variable, (mode, value) in scenario.items():
        if variable not in result.columns:
            raise ValueError(t("addon.modelling.unknown_variable", variable=variable))
        if mode == "add":
            result[variable] = result[variable] + value
        elif mode == "mul":
            result[variable] = result[variable] * value
        elif mode == "set":
            result[variable] = value
        else:
            raise ValueError(f"mode: {mode}")
    result.attrs.update(frame.attrs)
    return result


def simulate(predict: Callable[[pd.DataFrame], pd.Series], frame: pd.DataFrame, scenario: Scenario) -> pd.DataFrame:
    baseline = predict(frame)
    scenario_result = predict(apply_scenario(frame, scenario))
    return pd.DataFrame({"baseline": baseline, "scenario": scenario_result, "difference": scenario_result - baseline})


def sensitivity(
    predict: Callable[[pd.DataFrame], pd.Series], frame: pd.DataFrame, variable: str, mode: str,
    start: float, stop: float, steps: int,
) -> pd.DataFrame:
    """Gemiddeld modelresultaat voor een reeks waarden van één variabele."""
    rows = []
    for value in np.linspace(start, stop, max(steps, 2)):
        result = predict(apply_scenario(frame, {variable: (mode, float(value))}))
        rows.append({"value": float(value), "mean": float(result.mean()), "sum": float(result.sum())})
    return pd.DataFrame(rows)
