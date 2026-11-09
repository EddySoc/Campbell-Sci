from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from matplotlib.figure import Figure

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from logdef_editor import LogdefEditor, chart_to_line
from viewer import CampbellViewer


class BarChartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = CampbellViewer.__new__(CampbellViewer)
        self.app.header_row_count = 0
        self.data = pd.DataFrame({"time": ["2025-01-01", "2025-01-02"], "low": [-3, 2],
                                  "mean": [1, 4], "high": [5, 6]})
        self.app.pivot_columns = {}
        self.app.pivot_frames = {}
        self.headers = {name.upper(): name for name in self.data.columns}
        self.spec = {"type": "bar_range", "columns": ["time", "low", "mean", "high"],
                     "minimum": None, "maximum": None}

    def test_range_segments_and_mean_marker(self) -> None:
        axes = Figure().add_subplot()
        self.app._draw_special_bars(axes, self.spec, self.headers, self.data)
        self.assertEqual([(p.get_y(), p.get_height()) for p in axes.patches],
                         [(-3, 4), (2, 2), (1, 4), (4, 2)])
        self.assertEqual([p._campbell_target for p in axes.patches],
                         [(0, "low"), (1, "low"), (0, "high"), (1, "high")])
        self.assertEqual(axes.collections[0].get_offsets()[:, 1].tolist(), [1, 4])
        self.assertEqual(axes.collections[0]._campbell_column, "mean")
        zero = axes.get_lines()[0]
        self.assertEqual(list(zero.get_ydata()), [0, 0])
        self.assertGreater(zero.get_zorder(), axes.patches[0].get_zorder())
        self.assertLess(axes.get_ylim()[0], -3)

    def test_zero_visible_after_autoscale_and_fixed_limits_preserved(self) -> None:
        axes = Figure().add_subplot()
        self.app._draw_special_bars(axes, self.spec, self.headers, self.data)
        canvas = SimpleNamespace(_campbell_chart_spec=self.spec)
        axes.set_ylim(1, 2)
        self.app._fit_y_to_view(canvas, [axes])
        self.assertLess(axes.get_ylim()[0], 0)
        self.assertGreater(axes.get_ylim()[1], 0)
        self.spec["minimum"] = 1
        axes.set_ylim(1, 8)
        self.app._fit_y_to_view(canvas, [axes])
        self.assertEqual(axes.get_ylim(), (1, 8))

    def test_grouped_bars_do_not_overlap(self) -> None:
        axes = Figure().add_subplot()
        self.spec["type"] = "bar_grouped"
        self.app._draw_special_bars(axes, self.spec, self.headers, self.data)
        first = [axes.patches[i] for i in (0, 2, 4)]
        self.assertTrue(all(left.get_x() + left.get_width() <= right.get_x() + 1e-10
                            for left, right in zip(first, first[1:])))
        self.assertEqual([patch.get_height() for patch in first], [-3, 1, 5])
        self.assertEqual([patch._campbell_target for patch in first], [(0, "low"), (0, "mean"), (0, "high")])

    def test_range_validation(self) -> None:
        self.data.loc[0, "mean"] = 9
        with self.assertRaises(ValueError) as error:
            self.app._draw_special_bars(Figure().add_subplot(), self.spec, self.headers, self.data)
        self.assertIn("mean", str(error.exception))
        self.assertIn("9", str(error.exception))
        self.assertIn("2025-01-01", str(error.exception))
        self.data.loc[0, "mean"] = 1
        self.app.pivot_columns = {"high": "day"}
        self.app.pivot_frames = {"day": self.data.copy()}
        with self.assertRaises(ValueError):
            self.app._draw_special_bars(Figure().add_subplot(), self.spec, self.headers, self.data)

    def test_pivot_range_and_grouped_different_timebases(self) -> None:
        pivot = pd.DataFrame({"time": pd.to_datetime(["2025-01-01", "2025-01-02"]),
                              "pmin": [-4, 1], "pmean": [0, 3], "pmax": [6, 8]})
        self.app.pivot_frames = {"day": pivot}
        self.app.pivot_columns = {column: "day" for column in ("pmin", "pmean", "pmax")}
        self.headers.update({column.upper(): column for column in self.app.pivot_columns})
        self.spec["columns"] = ["time", "pmin", "pmean", "pmax"]
        axes = Figure().add_subplot()
        self.app._draw_special_bars(axes, self.spec, self.headers, self.data)
        self.assertEqual([(p.get_y(), p.get_height()) for p in axes.patches],
                         [(-4, 4), (1, 2), (0, 6), (3, 5)])
        self.spec["type"] = "bar_grouped"
        self.spec["columns"] = ["time", "low", "pmean"]
        axes = Figure().add_subplot()
        self.app._draw_special_bars(axes, self.spec, self.headers, self.data)
        self.assertEqual([p.get_height() for p in axes.patches], [-3, 2, 0, 3])

    def test_editor_roundtrip_and_validation(self) -> None:
        import tkinter as tk

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "TEST.logdef"
            root = tk.Tk()
            try:
                editor = LogdefEditor(root, list(self.data.columns), "TEST", Path(directory))
                editor.type_var.set(editor.type_labels["bar_range"])
                editor._update_hint()
                for column in ("low", "mean", "high"):
                    editor.selected.insert("end", column)
                with patch("logdef_editor.filedialog.asksaveasfilename", return_value=str(path)), \
                     patch("logdef_editor.messagebox.showinfo"):
                    editor._save()
                self.assertEqual(path.read_text(encoding="utf-8").splitlines()[-1].split(";")[1:3],
                                 ["bar_range", "time,low,mean,high"])
                self.assertFalse(editor.winfo_exists())
                editor = LogdefEditor(root, list(self.data.columns), "TEST", Path(directory))
                loaded = editor._read_charts(path)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded[0]["columns"], ["low", "mean", "high"])
                self.assertEqual(chart_to_line(loaded[0], "time").split(";")[2], "time,low,mean,high")
                editor._set_charts(loaded)
                self.assertEqual(editor.type_var.get(), editor.type_labels["bar_range"])
                editor.selected.selection_set(2)
                editor._move_selected(-1)
                self.assertEqual(editor.selected.get(0, "end"), ("low", "high", "mean"))
                self.assertEqual(editor.selected.curselection(), (1,))
                editor._move_selected(1)
                self.assertEqual(editor.selected.get(0, "end"), ("low", "mean", "high"))
                self.assertEqual(editor.selected.curselection(), (2,))
                editor.selected.selection_clear(0, "end")
                editor.selected.selection_set(0, 1)
                editor._move_selected(1)
                self.assertEqual(editor.selected.get(0, "end"), ("high", "low", "mean"))
                self.assertEqual(editor.selected.curselection(), (1, 2))
                editor._move_selected(-1)
                self.assertEqual(editor.selected.get(0, "end"), ("low", "mean", "high"))
                editor.selected.delete(2)
                with patch("logdef_editor.messagebox.showwarning") as warning, \
                     patch("logdef_editor.filedialog.asksaveasfilename") as save_dialog:
                    editor._save()
                warning.assert_called_once()
                save_dialog.assert_not_called()
                editor.type_var.set(editor.type_labels["bar_grouped"])
                with patch("logdef_editor.filedialog.asksaveasfilename") as save_dialog, \
                     patch("logdef_editor.messagebox.showinfo"):
                    editor._save()
                save_dialog.assert_not_called()
                self.assertFalse(editor.winfo_exists())
                editor = LogdefEditor(root, list(self.data.columns), "TEST", Path(directory))
                grouped = editor._read_charts(path)
                self.assertIsNotNone(grouped)
                self.assertEqual(grouped[0]["type"], "bar_grouped")
                self.assertEqual(grouped[0]["columns"], ["low", "mean"])
            finally:
                root.update_idletasks()
                root.destroy()


if __name__ == "__main__":
    unittest.main()
