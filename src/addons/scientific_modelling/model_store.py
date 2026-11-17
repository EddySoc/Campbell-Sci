from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .function_engine import evaluate, free_names
from .model_core import ModelSpec
from .physical_models import PHYSICAL_MODELS, run_physical_model
from i18n import t

FORMAT_VERSION = 1
SUFFIX = ".modeldef"


def spec_to_dict(spec: ModelSpec) -> dict:
    return {
        "version": FORMAT_VERSION, "name": spec.name, "kind": spec.kind, "inputs": spec.inputs,
        "target": spec.target, "params": spec.params, "errors": spec.errors, "expression": spec.expression,
        "unit": spec.unit, "train_end": spec.train_end.isoformat() if spec.train_end is not None else None,
        "definition": spec.definition,
    }


def save_model(spec: ModelSpec, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(spec_to_dict(spec), indent=2, ensure_ascii=False), encoding="utf-8")


def _predict_linear(params: dict[str, float], inputs: list[str]):
    def predict(f: pd.DataFrame) -> pd.Series:
        values = np.full(len(f), params["intercept"])
        for column in inputs:
            values = values + params[column] * f[column].to_numpy(float)
        return pd.Series(values, index=f.index, name="model")
    return predict


def _predict_polynomial(params: dict[str, float], variable: str):
    powers = sorted((int(k[1:]) for k in params if k.startswith("c") and k[1:].isdigit()), reverse=True)
    coefficients = [params[f"c{p}"] for p in powers]

    def predict(f: pd.DataFrame) -> pd.Series:
        scaled = (f[variable].to_numpy(float) - params["x_center"]) / params["x_scale"]
        return pd.Series(np.polyval(coefficients, scaled), index=f.index, name="model")
    return predict


def _predict_formula(expression: str, params: dict[str, float], variables: list[str]):
    def predict(f: pd.DataFrame) -> pd.Series:
        namespace: dict = {v: f[v].to_numpy(float) for v in variables}
        namespace.update(params)
        values = np.broadcast_to(np.asarray(evaluate(expression, namespace), float), (len(f),)).copy()
        return pd.Series(values, index=f.index, name="model")
    return predict


def spec_from_dict(raw: dict, available: set[str] | list[str]) -> ModelSpec:
    """Bouwt een model terug op uit opgeslagen parameters (geen nieuwe fit)."""
    try:
        kind, params = raw["kind"], {k: float(v) for k, v in raw.get("params", {}).items()}
        inputs, definition = list(raw.get("inputs", [])), dict(raw.get("definition") or {})
        expression = raw.get("expression", "")
        if kind == "linear":
            predict = _predict_linear(params, inputs)
        elif kind == "polynomial":
            predict = _predict_polynomial(params, inputs[0])
        elif kind == "formula":
            variables = [n for n in free_names(expression) if n not in params]
            inputs = variables
            predict = _predict_formula(expression, params, variables)
        elif kind == "physical":
            model = PHYSICAL_MODELS[definition["physical_key"]]
            mapping = dict(definition["mapping"])
            inputs = list(dict.fromkeys(mapping.values()))
            predict = lambda f: run_physical_model(model, mapping, params, f)  # noqa: E731
        else:
            raise ValueError(kind)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(t("addon.modelling.invalid_model_file")) from exc
    missing = [name for name in inputs if name not in available]
    if missing:
        raise ValueError(t("addon.modelling.this_model_uses_variables_that_are", missing=', '.join(missing)))
    train_end = pd.Timestamp(raw["train_end"]) if raw.get("train_end") else None
    name = raw.get("name") or kind
    if kind == "physical":
        name = t(PHYSICAL_MODELS[definition["physical_key"]].name)
    return ModelSpec(
        name=name, kind=kind, inputs=inputs, predict=predict, target=raw.get("target"), params=params,
        errors={k: float(v) for k, v in raw.get("errors", {}).items()}, expression=expression,
        unit=raw.get("unit", ""), train_end=train_end, definition=definition,
    )


def load_model(path: str | Path, available: set[str] | list[str]) -> ModelSpec:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(t("addon.modelling.model_file_could_not_be_read")) from exc
    if not isinstance(raw, dict):
        raise ValueError(t("addon.modelling.invalid_model_file"))
    return spec_from_dict(raw, available)
