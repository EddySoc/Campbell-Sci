from __future__ import annotations

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from logdef_editor import LogdefEditor
import viewer
from viewer import CampbellViewer, load_chart_config


class ScatterTrendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = CampbellViewer.__new__(CampbellViewer)
        self.app.pivot_columns = {}
        self.app.pivot_frames = {}
        self.headers = {"Y": "y", "X": "x"}
        self.spec = {"columns": ["y", "x"], "trend": "linear"}

    def draw(self, x: list[float], y: list[float]):
        data = pd.DataFrame({"x": x, "y": y})
        axes = Figure().add_subplot()
        self.app._draw_scatter(axes, self.spec, self.headers, data)
        return axes

    def test_linear_regression_and_missing_values(self) -> None:
        axes = self.draw([1, 2, 3, np.nan, np.inf], [5, 7, 9, 100, 4])
        self.assertEqual(axes.get_xlabel(), "x")
        self.assertEqual(axes.get_ylabel(), "y")
        self.assertEqual(axes.get_lines()[0].get_ydata().tolist(), [5, 9])
        self.assertIn("y = 2x +3", axes.texts[0].get_text())
        self.assertIn("R² = 1", axes.texts[0].get_text())
        self.assertEqual(len(axes.collections[0].get_offsets()), 3)

    def test_constant_x_and_constant_y(self) -> None:
        axes = self.draw([2, 2], [1, 3])
        self.assertEqual(len(axes.collections[0].get_offsets()), 2)
        self.assertEqual(len(axes.get_lines()), 0)
        self.assertIn("trendlijn", axes.texts[0].get_text().lower())
        axes = self.draw([1, 2], [3, 3])
        self.assertIn("niet gedefinieerd", axes.texts[0].get_text())

    def test_trend_disabled_by_default(self) -> None:
        self.spec.pop("trend")
        axes = self.draw([1, 2], [3, 5])
        self.assertEqual(len(axes.get_lines()), 0)
        self.assertEqual(len(axes.texts), 0)

    def test_autoscale_includes_scatter_points_not_only_trend(self) -> None:
        axes = self.draw([1, 2, 3], [0, 10, 0])
        axes.set_xlim(0.5, 3.5)
        self.app._fit_y_to_view(SimpleNamespace(_campbell_chart_spec=self.spec), [axes])
        bottom, top = axes.get_ylim()
        self.assertLess(bottom, 0)
        self.assertGreater(top, 10)

    def test_editor_save_reload_and_legacy_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "TEST.logdef"
            path.write_text("Old;scatter;y,x;;;0.0;\n", encoding="utf-8")
            root = tk.Tk()
            try:
                editor = LogdefEditor(root, ["time", "y", "x"], "TEST", Path(directory))
                self.assertIn("disabled", editor.secondary_button.state())
                self.assertIn("Y", editor.hint.get())
                self.assertFalse(editor.trend_var.get())
                editor.trend_var.set(True)
                editor._save()
                loaded = load_chart_config("TEST", Path(directory))
                self.assertEqual(loaded[0]["columns"], ["y", "x"])
                self.assertEqual(loaded[0]["trend"], "linear")
                editor = LogdefEditor(root, ["time", "y", "x"], "TEST", Path(directory))
                self.assertTrue(editor.trend_var.get())
                self.assertEqual(list(editor.selected.get(0, "end")), ["y", "x"])
                editor.type_var.set(editor.type_labels["line"])
                editor._update_hint()
                self.assertNotIn("disabled", editor.secondary_button.state())
                self.assertIn("disabled", editor.trend_button.state())
            finally:
                root.update_idletasks()
                root.destroy()

    def test_saving_refreshes_actual_scatter_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            path = config / "TEST.logdef"
            path.write_text("Old;scatter;y,x;;;0.0;\n", encoding="utf-8")
            data = pd.DataFrame({"time": ["2025-01-01", "2025-01-02", "2025-01-03"],
                                 "x": [1, 2, 3], "y": [5, 7, 9]})
            data.attrs["header_row_count"] = 0
            with patch.object(viewer, "LOGDEF_DIR", config), \
                 patch.object(viewer, "FORMULA_DIR", config / "formulas"), \
                 patch.object(viewer, "PIVOT_DIR", config / "pivots"), \
                 patch.object(viewer, "PIVOT_FORMULA_DIR", config / "pivot_formulas"), \
                 patch.object(viewer, "FUNCTION_DIR", config / "functions"), \
                 patch.object(CampbellViewer, "_configure_window", lambda self: self.root.geometry("600x400")), \
                 patch.object(CampbellViewer, "_apply_window_icon", lambda self: None), \
                 patch.object(CampbellViewer, "_activate_window", lambda self: None):
                root = tk.Tk()
                try:
                    app = CampbellViewer(root, data, Path("TEST.dat"), "TEST")
                    root.update()
                    self.assertEqual(len(app.chart_axes[0][1][0].get_lines()), 0)
                    editor = LogdefEditor(root, list(data.columns), "TEST", config, on_saved=app._reload_logdef)
                    editor.trend_var.set(True)
                    editor._save()
                    root.update()
                    axes = app.chart_axes[0][1][0]
                    self.assertEqual(len(axes.get_lines()), 1)
                    self.assertIn("y = 2x +3", axes.texts[0].get_text())
                finally:
                    root.update_idletasks()
                    root.destroy()

    def test_invalid_trend_configuration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "TEST.logdef"
            path.write_text("Old;line;time,y;;;0.0;;linear\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_chart_config("TEST", Path(directory))

    def test_scatter_requires_exactly_two_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            try:
                editor = LogdefEditor(root, ["time", "y", "x", "z"], "TEST", Path(directory))
                editor.type_var.set(editor.type_labels["scatter"])
                editor._update_hint()
                for column in ("y", "x", "z"):
                    editor.selected.insert("end", column)
                with patch("logdef_editor.messagebox.showwarning") as warning, \
                     patch("logdef_editor.filedialog.asksaveasfilename") as dialog:
                    editor._save()
                warning.assert_called_once()
                dialog.assert_not_called()
            finally:
                root.update_idletasks()
                root.destroy()


if __name__ == "__main__":
    unittest.main()
