from __future__ import annotations

import numpy as np
import pandas as pd

from .data_analysis import split_chronological
from .function_engine import evaluate, free_names
from .model_core import ModelSpec
from i18n import t


def _train(frame: pd.DataFrame, fraction: float) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    train, _test = split_chronological(frame, fraction)
    return train, (train.index[-1] if fraction < 1.0 else None)


def _too_few() -> ValueError:
    return ValueError(t("addon.modelling.too_few_complete_data_points_to"))


def fit_linear(frame: pd.DataFrame, inputs: list[str], target: str, train_fraction: float = 1.0) -> ModelSpec:
    if not inputs:
        raise ValueError(t("addon.modelling.choose_at_least_one_independent_variable"))
    train, train_end = _train(frame, train_fraction)
    data = train[inputs + [target]].dropna()
    if len(data) < len(inputs) + 2:
        raise _too_few()
    design = np.column_stack([np.ones(len(data))] + [data[c].to_numpy(float) for c in inputs])
    observed = data[target].to_numpy(float)
    coefficients, *_ = np.linalg.lstsq(design, observed, rcond=None)
    residuals = observed - design @ coefficients
    variance = residuals @ residuals / max(len(data) - design.shape[1], 1)
    errors = np.sqrt(np.clip(np.diag(variance * np.linalg.pinv(design.T @ design)), 0, None))
    names = ["intercept"] + inputs

    def predict(f: pd.DataFrame) -> pd.Series:
        values = np.full(len(f), coefficients[0])
        for column, coefficient in zip(inputs, coefficients[1:]):
            values = values + coefficient * f[column].to_numpy(float)
        return pd.Series(values, index=f.index, name="model")

    expression = " + ".join(
        [f"{coefficients[0]:.4g}"] + [f"{b:.4g}*{c}" for c, b in zip(inputs, coefficients[1:])]
    )
    return ModelSpec(
        name=t("addon.modelling.linear_regression"), kind="linear", inputs=list(inputs), target=target,
        predict=predict, params=dict(zip(names, map(float, coefficients))),
        errors=dict(zip(names, map(float, errors))), expression=f"{target} = {expression}", train_end=train_end,
    )


def fit_polynomial(frame: pd.DataFrame, variable: str, target: str, degree: int, train_fraction: float = 1.0) -> ModelSpec:
    if degree < 1:
        raise ValueError(t("addon.modelling.degree_must_be_at_least_1"))
    train, train_end = _train(frame, train_fraction)
    data = train[[variable, target]].dropna()
    if len(data) <= degree + 1:
        raise _too_few()
    x_values = data[variable].to_numpy(float)
    center, scale = x_values.mean(), x_values.std() or 1.0
    coefficients = np.polyfit((x_values - center) / scale, data[target].to_numpy(float), degree)

    def predict(f: pd.DataFrame) -> pd.Series:
        scaled = (f[variable].to_numpy(float) - center) / scale
        return pd.Series(np.polyval(coefficients, scaled), index=f.index, name="model")

    params = {f"c{degree - i}": float(c) for i, c in enumerate(coefficients)}
    params.update({"x_center": float(center), "x_scale": float(scale)})
    return ModelSpec(
        name=t("addon.modelling.polynomial_degree_2", degree=degree), kind="polynomial",
        inputs=[variable], target=target, predict=predict, params=params,
        expression=f"{target} = polynoom((({variable} - x_center) / x_scale), c0..c{degree})", train_end=train_end,
    )


def fit_formula(
    frame: pd.DataFrame, expression: str, target: str | None, start_values: dict[str, float],
    fit: bool = True, train_fraction: float = 1.0,
) -> ModelSpec:
    """Eigen formule; onbekende namen zijn parameters. Met fit=True worden ze niet-lineair gefit."""
    names = free_names(expression)
    variables = [n for n in names if n in frame.columns]
    parameters = [n for n in names if n not in frame.columns]
    if not variables:
        raise ValueError(t("addon.modelling.the_formula_uses_no_measured_variable"))

    def run(f: pd.DataFrame, values: list[float]) -> np.ndarray:
        namespace: dict = {v: f[v].to_numpy(float) for v in variables}
        namespace.update(dict(zip(parameters, values)))
        return np.broadcast_to(np.asarray(evaluate(expression, namespace), float), (len(f),)).copy()

    errors: dict[str, float] = {}
    train_end = None
    if fit and parameters:
        if not target:
            raise ValueError(t("addon.modelling.choose_a_target_variable_to_fit"))
        from scipy.optimize import curve_fit

        train, train_end = _train(frame, train_fraction)
        data = train[variables + [target]].dropna()
        if len(data) < len(parameters) + 2:
            raise _too_few()
        try:
            optimum, covariance = curve_fit(
                lambda _x, *p: run(data, list(p)), np.arange(len(data)), data[target].to_numpy(float),
                p0=[start_values.get(p, 1.0) for p in parameters], maxfev=20000,
            )
        except (RuntimeError, ValueError) as exc:
            raise ValueError(t("addon.modelling.fit_failed", exc=exc)) from exc
        spread = np.sqrt(np.diag(covariance)) if np.all(np.isfinite(covariance)) else np.full(len(parameters), np.nan)
        values = [float(v) for v in optimum]
        errors = dict(zip(parameters, map(float, spread)))
    else:
        missing = [p for p in parameters if p not in start_values]
        if missing:
            raise ValueError(t("addon.modelling.provide_values_for_parameters", missing=', '.join(missing)))
        values = [float(start_values[p]) for p in parameters]

    def predict(f: pd.DataFrame) -> pd.Series:
        return pd.Series(run(f, values), index=f.index, name="model")

    return ModelSpec(
        name=t("addon.modelling.custom_formula"), kind="formula", inputs=variables, target=target,
        predict=predict, params=dict(zip(parameters, values)), errors=errors,
        expression=expression.strip(), train_end=train_end,
    )
