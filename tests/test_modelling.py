from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from addons.scientific_modelling import data_analysis as da
from addons.scientific_modelling import regression, time_series, validation
from addons.scientific_modelling.function_engine import evaluate, free_names
from addons.scientific_modelling.physical_models import PHYSICAL_MODELS, run_physical_model
from addons.scientific_modelling.simulation import simulate


def make_frame(days: int = 6) -> pd.DataFrame:
    index = pd.date_range("2024-06-01", periods=days * 24, freq="h")
    rng = np.random.default_rng(1)
    hour = index.hour.to_numpy()
    radiation = np.clip(800 * np.sin((hour - 6) / 12 * np.pi), 0, None)
    temp = 10 + 0.01 * radiation + rng.normal(0, 0.1, len(index))
    humidity = 80 - 0.02 * radiation
    wind = 3 + rng.random(len(index))
    data = pd.DataFrame({
        "TIMESTAMP": ["TS", ""] + [str(t) for t in index],
        "Temp(1)": ["C", "Avg"] + list(temp),
        "Rad": ["W", "Avg"] + list(radiation),
        "RH": ["%", "Avg"] + list(humidity),
        "Wind": ["m/s", "Avg"] + list(wind),
    })
    return da.numeric_frame(data, 2)


class ModellingTests(unittest.TestCase):
    def test_numeric_frame_sanitizes_and_adds_time_variables(self) -> None:
        frame = make_frame()
        self.assertIn("Temp_1", frame.columns)
        self.assertEqual(frame.attrs["names"]["Temp_1"], "Temp(1)")
        self.assertTrue({"time_days", "hour", "doy"} <= set(frame.columns))

    def test_formula_engine_rejects_unsafe_code(self) -> None:
        self.assertEqual(free_names("a*Temp + exp(b)"), ["Temp", "a", "b"])
        with self.assertRaises(ValueError):
            evaluate("__import__('os').system('x')", {})
        with self.assertRaises(ValueError):
            evaluate("x.real", {"x": 1})

    def test_linear_fit_with_train_test_split(self) -> None:
        frame = make_frame()
        model = regression.fit_linear(frame, ["Rad"], "Temp_1", 0.7)
        self.assertAlmostEqual(model.params["Rad"], 0.01, places=3)
        self.assertIsNotNone(model.train_end)
        test = frame[frame.index > model.train_end]
        metrics = validation.metrics(validation.paired(test["Temp_1"], model.predict(test)))
        self.assertGreater(metrics["R2"], 0.99)

    def test_nonlinear_formula_fit(self) -> None:
        frame = make_frame()
        model = regression.fit_formula(frame, "a + b*Rad", "Temp_1", {"a": 0.0, "b": 0.0})
        self.assertAlmostEqual(model.params["b"], 0.01, places=3)

    def test_physical_models_and_scenario(self) -> None:
        frame = make_frame()
        daily = frame.resample("D").mean()
        model = PHYSICAL_MODELS["penman_monteith"]
        mapping = {"T": "Temp_1", "RH": "RH", "u": "Wind", "Rs": "Rad"}
        params = {k: d for k, _l, d in model.params}
        et0 = run_physical_model(model, mapping, params, daily)
        self.assertTrue(et0.between(0, 15).all())
        warmer = simulate(lambda f: run_physical_model(model, mapping, params, f), daily, {"Temp_1": ("add", 2.0)})
        self.assertTrue((warmer["difference"] > 0).all())

    def test_model_save_load_roundtrip(self) -> None:
        import tempfile

        from addons.scientific_modelling import model_store
        from addons.scientific_modelling.model_core import ModelSpec

        frame = make_frame()
        pm = PHYSICAL_MODELS["hargreaves"]
        mapping = {"T": "Temp_1", "Tmax": "Temp_1", "Tmin": "RH"}
        params = {k: d for k, _l, d in pm.params}
        physical = ModelSpec(
            "x", "physical", list(mapping.values()), lambda f: run_physical_model(pm, mapping, params, f),
            params=params, definition={"physical_key": pm.key, "mapping": mapping},
        )
        models = [
            regression.fit_linear(frame, ["Rad", "RH"], "Temp_1", 0.7),
            regression.fit_polynomial(frame, "Rad", "Temp_1", 2),
            regression.fit_formula(frame, "a + b*Rad", "Temp_1", {"a": 0.0, "b": 0.0}),
            physical,
        ]
        with tempfile.TemporaryDirectory() as folder:
            for index, model in enumerate(models):
                path = Path(folder) / f"m{index}{model_store.SUFFIX}"
                model_store.save_model(model, path)
                loaded = model_store.load_model(path, set(frame.columns))
                pd.testing.assert_series_equal(model.predict(frame), loaded.predict(frame), check_names=False)
                self.assertEqual(model.train_end, loaded.train_end)
            with self.assertRaises(ValueError):
                model_store.load_model(Path(folder) / f"m0{model_store.SUFFIX}", {"Temp_1"})

    def test_time_series(self) -> None:
        frame = make_frame()
        parts = time_series.decompose(frame["Temp_1"], "day")
        self.assertGreater(parts["seasonal"].std(), 1.0)
        self.assertLess(abs(time_series.trend(frame["Temp_1"])["slope_per_day"]), 1.0)


if __name__ == "__main__":
    unittest.main()
