from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from formulas import apply_formulas, save_formulas
from pivots import calculate_pivots, load_pivots, save_pivots


class PivotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = pd.DataFrame({
            "TIMESTAMP": ["units", "processing", "2024-12-30 00:00", "2024-12-30 00:30",
                          "2024-12-31 00:00", "2025-01-01 00:00", "2025-01-06 00:00"],
            "value": ["C", "Avg", 2, 4, 6, 8, None],
        })

    def definition(self, period: str = "day", aggregation: str = "mean") -> dict[str, str]:
        return {"name": f"{period}_{aggregation}", "column": "value", "period": period, "aggregation": aggregation}

    def test_periods_and_aggregations(self) -> None:
        definitions = [self.definition(p) for p in ("minute", "hour", "day", "week", "month", "year")]
        definitions += [self.definition("day", a) for a in ("sum", "min", "max", "count", "median")]
        frames = calculate_pivots(self.data, 2, definitions)
        day = frames["day"]
        self.assertEqual(list(day["TIMESTAMP"][:3]), list(pd.to_datetime(["2024-12-30", "2024-12-31", "2025-01-01"])))
        self.assertEqual(day.loc[0, "day_mean"], 3)
        self.assertEqual(day.loc[0, "day_sum"], 6)
        self.assertEqual(day.loc[0, "day_count"], 2)
        self.assertEqual(day.loc[0, "day_min"], 2)
        self.assertEqual(day.loc[0, "day_max"], 4)
        self.assertEqual(day.loc[0, "day_median"], 3)
        self.assertTrue(pd.isna(day.loc[3, "day_sum"]))
        self.assertEqual(frames["week"].loc[0, "TIMESTAMP"], pd.Timestamp("2024-12-30"))
        self.assertEqual(frames["week"].loc[0, "week_mean"], 5)
        self.assertEqual(frames["month"].loc[0, "TIMESTAMP"], pd.Timestamp("2024-12-01"))
        self.assertEqual(frames["year"].loc[1, "TIMESTAMP"], pd.Timestamp("2025-01-01"))
        self.assertEqual(len(frames["minute"]), 5)
        self.assertEqual(len(frames["hour"]), 4)

    def test_formulas_use_pivot_rows(self) -> None:
        frame = calculate_pivots(self.data, 2, [self.definition()])["day"]
        result, names, errors = apply_formulas(frame, [{"name": "double", "expression": "[day_mean] * 2"}], 0)
        self.assertEqual(names, ["double"])
        self.assertEqual(errors, [])
        self.assertEqual(result["double"][:3].tolist(), [6, 12, 16])
        self.assertEqual(len(result), 4)

    def test_mean_uses_all_measurements_not_only_extremes(self) -> None:
        data = pd.DataFrame({"time": ["2025-01-01 00:00", "2025-01-01 01:00", "2025-01-01 02:00"],
                             "value": [0, 9, 9]})
        frame = calculate_pivots(data, 0, [self.definition()])["day"]
        self.assertEqual(frame.loc[0, "day_mean"], 6)
        self.assertNotEqual(frame.loc[0, "day_mean"], (data["value"].min() + data["value"].max()) / 2)

    def test_file_roundtrip_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "BM.pivotdef"
            definitions = [self.definition()]
            save_pivots(path, definitions)
            self.assertEqual(load_pivots(path), definitions)
            save_pivots(path, [])
            self.assertTrue(path.with_name(path.name + ".bak").is_file())
            with self.assertRaises(ValueError):
                calculate_pivots(self.data, 2, [self.definition(), self.definition()])
            with self.assertRaises(ValueError):
                calculate_pivots(self.data, 2, [{"name": "value", "column": "value", "period": "day", "aggregation": "mean"}])

    def test_invalid_times_are_reported(self) -> None:
        self.data.loc[2, "TIMESTAMP"] = "invalid"
        with self.assertRaisesRegex(ValueError, "tijdstip"):
            calculate_pivots(self.data, 2, [self.definition()])


class ViewerPivotTests(unittest.TestCase):
    def test_move_pivots_preserves_edits_selection_and_saved_column_order(self) -> None:
        import tkinter as tk
        from pivot_editor import PivotEditor

        data = pd.DataFrame({"time": ["2025-01-01", "2025-01-02"], "value": [1, 3]})
        definitions = [
            {"name": "first", "column": "value", "period": "day", "aggregation": "mean"},
            {"name": "second", "column": "value", "period": "day", "aggregation": "max"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UNIT.pivotdef"
            save_pivots(path, definitions)
            root = tk.Tk()
            try:
                editor = PivotEditor(root, data, 0, path, lambda: None)
                editor._move(-1)
                self.assertEqual(editor.current, 0)
                editor.name.set("renamed")
                editor._move(1)
                self.assertEqual(editor.current, 1)
                self.assertEqual(editor.listbox.curselection(), (1,))
                self.assertEqual(list(editor.listbox.get(0, "end")), ["second", "renamed"])
                editor._move(1)
                self.assertEqual(editor.current, 1)
                editor._save()
                saved = load_pivots(path)
                self.assertEqual([item["name"] for item in saved], ["second", "renamed"])
                frame = calculate_pivots(data, 0, saved)["day"]
                self.assertEqual(list(frame.columns), ["time", "second", "renamed"])
            finally:
                root.destroy()

    def test_move_empty_new_pivot_preserves_new_status(self) -> None:
        import tkinter as tk
        from pivot_editor import PivotEditor

        data = pd.DataFrame({"time": ["2025-01-01"], "value": [1]})
        definition = {"name": "existing", "column": "value", "period": "day", "aggregation": "mean"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UNIT.pivotdef"
            save_pivots(path, [definition])
            root = tk.Tk()
            try:
                editor = PivotEditor(root, data, 0, path, lambda: None)
                editor._add()
                editor._move(-1)
                self.assertEqual(editor.current, 0)
                self.assertEqual(editor.new_indices, {0})
                editor._save()
                self.assertEqual(load_pivots(path), [definition])
            finally:
                root.destroy()

    def test_incomplete_pivot_identifies_and_selects_missing_field(self) -> None:
        import tkinter as tk
        from pivot_editor import PivotEditor

        data = pd.DataFrame({"time": ["2025-01-01", "2025-01-02"], "value": [1, 3]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UNIT.pivotdef"
            root = tk.Tk()
            try:
                editor = PivotEditor(root, data, 0, path, lambda: None)
                editor.name.set("daily")
                editor._add()
                editor.name.set("weekly")
                editor.column.set("value")
                with patch("pivot_editor.messagebox.showerror") as error:
                    editor._save()
                self.assertFalse(path.exists())
                self.assertEqual(editor.current, 0)
                message = error.call_args.args[1]
                self.assertIn("daily", message)
                self.assertIn("Bronkolom", message)
                editor.column.set("value")
                editor._save()
                self.assertEqual([item["name"] for item in load_pivots(path)], ["daily", "weekly"])
            finally:
                root.destroy()

    def test_empty_new_pivot_is_not_saved(self) -> None:
        import tkinter as tk
        from pivot_editor import PivotEditor

        data = pd.DataFrame({"time": ["2025-01-01"], "value": [1]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UNIT.pivotdef"
            root = tk.Tk()
            try:
                editor = PivotEditor(root, data, 0, path, lambda: None)
                editor.name.set("daily")
                editor.column.set("value")
                editor._add()
                with patch("pivot_editor.messagebox.showerror") as error:
                    editor._save()
                error.assert_not_called()
                self.assertEqual([item["name"] for item in load_pivots(path)], ["daily"])
            finally:
                root.destroy()

    def test_existing_pivot_cleared_does_not_disappear(self) -> None:
        import tkinter as tk
        from pivot_editor import PivotEditor

        data = pd.DataFrame({"time": ["2025-01-01"], "value": [1]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UNIT.pivotdef"
            definitions = [{"name": "daily", "column": "value", "period": "day", "aggregation": "mean"}]
            save_pivots(path, definitions)
            root = tk.Tk()
            try:
                editor = PivotEditor(root, data, 0, path, lambda: None)
                editor.name.set("")
                editor.column.set("")
                with patch("pivot_editor.messagebox.showerror") as error:
                    editor._save()
                error.assert_called_once()
                self.assertEqual(load_pivots(path), definitions)
            finally:
                root.destroy()

    def test_new_pivot_with_changed_period_requires_missing_fields(self) -> None:
        import tkinter as tk
        from pivot_editor import PivotEditor

        data = pd.DataFrame({"time": ["2025-01-01"], "value": [1]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UNIT.pivotdef"
            root = tk.Tk()
            try:
                editor = PivotEditor(root, data, 0, path, lambda: None)
                editor.period.set(editor.period_labels[0])
                with patch("pivot_editor.messagebox.showerror") as error:
                    editor._save()
                error.assert_called_once()
                self.assertFalse(path.exists())
            finally:
                root.destroy()

    def test_scatter_matches_timestamps_and_single_bar(self) -> None:
        from matplotlib.figure import Figure
        import viewer

        app = viewer.CampbellViewer.__new__(viewer.CampbellViewer)
        app.header_row_count = 0
        data = pd.DataFrame({"time": ["2025-01-01 00:00", "2025-01-01 00:30", "2025-01-02 00:00"], "value": [2, 4, 8]})
        frame = calculate_pivots(data, 0, [{"name": "daily", "column": "value", "period": "day", "aggregation": "mean"}])["day"]
        app.pivot_frames = {"day": frame}
        app.pivot_columns = {"daily": "day"}
        headers = {"TIME": "time", "VALUE": "value", "DAILY": "daily"}
        fig = Figure()
        axes = fig.add_subplot()
        app._draw_scatter(axes, {"columns": ["daily", "value"]}, headers, data)
        self.assertEqual(axes.collections[0].get_offsets().tolist(), [[2, 3], [8, 8]])
        one = data.iloc[:1]
        app.pivot_frames["day"] = frame.iloc[:1]
        fig = Figure()
        axes = fig.add_subplot()
        spec = {"columns": ["time", "daily"], "type": "bar", "minimum": None, "maximum": None}
        app._draw_lines(fig, axes, spec, headers, one)
        self.assertEqual(len(axes.patches), 1)

    def test_editor_saves_definitions(self) -> None:
        import tkinter as tk
        from pivot_editor import PivotEditor

        data = pd.DataFrame({"time": ["2025-01-01", "2025-01-02"], "value": [1, 3]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UNIT.pivotdef"
            root = tk.Tk()
            saved: list[bool] = []
            try:
                editor = PivotEditor(root, data, 0, path, lambda: saved.append(True))
                editor.name.set("daily")
                editor.column.set("value")
                editor._save()
                self.assertEqual(saved, [True])
                self.assertEqual(load_pivots(path)[0]["period"], "day")
            finally:
                root.destroy()

    def test_independent_time_axes_and_scrollable_tabs(self) -> None:
        import tkinter as tk
        import viewer

        data = pd.DataFrame({
            "TIMESTAMP": ["2025-01-01 00:00", "2025-01-01 00:30", "2025-01-02 00:00"],
            "value": [2, 4, 8],
        })
        data.attrs["header_row_count"] = 0
        spec = {"name": "Mixed", "type": "line", "columns": ["TIMESTAMP", "value", "daily", "twice"],
                "minimum": None, "maximum": None, "number_format": "0.0", "tab_color": ""}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            save_pivots(path / "UNIT.pivotdef", [{"name": "daily", "column": "value", "period": "day", "aggregation": "mean"}])
            save_formulas(path / "formulas" / "day" / "UNIT.formuladef", [{"name": "twice", "expression": "[daily] * 2"}])
            with patch.object(viewer, "PIVOT_DIR", path), patch.object(viewer, "PIVOT_FORMULA_DIR", path / "formulas"), \
                    patch.object(viewer, "FORMULA_DIR", path / "calc"), patch.object(viewer, "FUNCTION_DIR", path / "functions"), \
                    patch.object(viewer, "load_chart_config", return_value=[spec] * 8), \
                    patch.object(viewer.CampbellViewer, "_configure_window", lambda self: self.root.geometry("600x400")), \
                    patch.object(viewer.CampbellViewer, "_apply_window_icon", lambda self: None), \
                    patch.object(viewer.CampbellViewer, "_activate_window", lambda self: None):
                root = tk.Tk()
                try:
                    app = viewer.CampbellViewer(root, data, Path("UNIT.dat"), "UNIT")
                    root.update()
                    lines = app.chart_axes[0][1][0].get_lines()
                    self.assertEqual([len(line.get_xdata()) for line in lines], [3, 2, 2])
                    self.assertEqual(list(lines[1].get_ydata()), [3, 8])
                    self.assertEqual(list(lines[2].get_ydata()), [6, 16])
                    self.assertEqual(app.pivot_columns["twice"], "day")
                    self.assertEqual(len(app.tab_strip.winfo_children()), 11)
                    self.assertLess(app.tab_canvas.xview()[1], 1)
                    app.tab_canvas.xview_moveto(1)
                    self.assertGreater(app.tab_canvas.xview()[0], 0)
                    app._go_to_data(app.canvases[0], (0, "daily"))
                    root.update()
                    self.assertEqual(app.notebook.index("current"), 2)
                    self.assertEqual(app.pivot_choice.current(), 0)
                    self.assertLess(app.tab_canvas.xview()[0], 0.5)
                    app.notebook.select(app.notebook.tabs()[0])
                    root.update()
                    app.data.loc[0, "value"] = 10
                    app._data_changed = True
                    app.notebook.select(app.notebook.tabs()[3])
                    root.update()
                    self.assertEqual(app.pivot_frames["day"].loc[0, "daily"], 7)
                    self.assertEqual(list(app.chart_axes[0][1][0].get_lines()[1].get_ydata()), [7, 8])
                    self.assertEqual(list(app.chart_axes[0][1][0].get_lines()[2].get_ydata()), [14, 16])
                finally:
                    root.destroy()


if __name__ == "__main__":
    unittest.main()
