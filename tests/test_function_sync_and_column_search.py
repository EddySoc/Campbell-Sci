from __future__ import annotations

import sys
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from formula_editor import FormulaEditor, _add_text_search_bar
from formulas import evaluate_formula, function_help, load_funcdefs, parse_funcdefs, save_formulas
from function_catalog import CATALOG_NAME, FunctionEntry, FunctionGroup, load_catalog, save_catalog, sync_catalog
from pivots import save_pivots
from viewer import ColumnSearch


class FunctionSyncTests(unittest.TestCase):
    def test_text_search_next_and_previous_wrap(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            window = tk.Toplevel(root)
            text = tk.Text(window)
            text.insert("1.0", "sensor A\nSENSOR B")
            entry, previous_button, next_button = _add_text_search_bar(window, text)
            text.pack(fill="both", expand=True)
            entry.insert(0, "sensor")
            next_button.invoke()
            self.assertEqual(text.index("find_current.first"), "1.0")
            next_button.invoke()
            self.assertEqual(text.index("find_current.first"), "2.0")
            next_button.invoke()
            self.assertEqual(text.index("find_current.first"), "1.0")
            previous_button.invoke()
            self.assertEqual(text.index("find_current.first"), "2.0")
        finally:
            root.destroy()

    def test_function_catalog_editor_saves_changes_from_formula_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            save_catalog(base / CATALOG_NAME, [FunctionGroup("Meteo", "#0066cc", [])])
            root = tk.Tk()
            root.withdraw()
            try:
                editor = FormulaEditor(root, pd.DataFrame({"time": [], "value": []}), 0,
                                       "UNIT", base, function_dir=base)
                editor._edit_catalog()
                window = next(child for child in editor.winfo_children() if isinstance(child, tk.Toplevel))
                text = next(child for child in window.winfo_children() if isinstance(child, tk.Text))
                content = text.get("1.0", "end-1c").replace('"color": "#0066cc"', '"color": "lightblue"')
                text.delete("1.0", "end")
                text.insert("1.0", content)
                next(child for child in window.winfo_children() if isinstance(child, ttk.Button)).invoke()
                self.assertEqual(load_catalog(base / CATALOG_NAME)[0].color, "lightblue")
            finally:
                root.destroy()

    def test_renamed_builtin_catalog_entries_keep_group_and_visibility(self) -> None:
        groups = [FunctionGroup("Custom", "#123456", [
            FunctionEntry("STAP", False), FunctionEntry("ZONOP"), FunctionEntry("ZONONDER", False)
        ])]
        sync_catalog(groups, list(function_help()), {})
        custom = groups[0]
        self.assertEqual([(entry.name, entry.visible) for entry in custom.functions],
                         [("STEP", False), ("SUNUP", True), ("SUNDOWN", False)])
        self.assertEqual(custom.color, "#123456")

        groups = [FunctionGroup("Custom", "#123456", [FunctionEntry("STAP", False), FunctionEntry("STEP")])]
        sync_catalog(groups, list(function_help()), {})
        self.assertEqual([(entry.name, entry.visible) for entry in groups[0].functions], [("STEP", True)])

    def test_saving_adds_removes_and_moves_functions_without_losing_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / "Functies.funcdef"
            path.write_text("Old(x) @ Meteo = [x]\nKeep(x) @ Meteo = [x]\n", encoding="utf-8")
            (base / "Extra.funcdef").write_text("Extra(x) @ Meteo = [x] * 2\n", encoding="utf-8")
            save_catalog(base / CATALOG_NAME, [
                FunctionGroup("Meteo", "#0066cc", [
                    FunctionEntry("Old"), FunctionEntry("Keep", False), FunctionEntry("Extra")
                ])
            ])
            root = tk.Tk()
            root.withdraw()
            try:
                editor = FormulaEditor(root, pd.DataFrame({"time": ["2025-01-01"], "x": [2]}),
                                       0, "UNIT", base, function_dir=base)
                editor._edit_funcdef()
                window = next(child for child in editor.winfo_children() if isinstance(child, tk.Toplevel))
                text_widgets = [child for child in window.winfo_children() if isinstance(child, tk.Text)]
                text = next(widget for widget in text_widgets if widget.cget("state") == "normal")
                line_numbers = next(widget for widget in text_widgets if widget.cget("state") == "disabled")
                text.delete("1.0", "end")
                text.insert("1.0", "Keep(x) @ Nieuw = [x] + 1\nNew(x) = [x] * 3\n")
                window.update()
                self.assertEqual(line_numbers.get("1.0", "end-1c").splitlines(), ["1", "2", "3"])
                button = next(child for child in window.winfo_children() if isinstance(child, ttk.Button))
                button.invoke()
                content = path.read_text(encoding="utf-8")
                self.assertIn("New(x) @ Overig =", content)
                groups = load_catalog(base / CATALOG_NAME)
                entries = {entry.name.upper(): (group, entry) for group in groups for entry in group.functions}
                self.assertNotIn("OLD", entries)
                self.assertEqual(entries["KEEP"][0].name, "Nieuw")
                self.assertFalse(entries["KEEP"][1].visible)
                self.assertEqual(entries["NEW"][0].name, "Overig")
                self.assertEqual(entries["EXTRA"][0].name, "Meteo")
                macros = load_funcdefs(base)
                result = evaluate_formula("Keep([x]) + New([x]) + Extra([x])", editor.data, 0, macros)
                self.assertEqual(result.iloc[0], 13)
            finally:
                root.destroy()

    def test_legacy_and_grouped_definitions_are_both_accepted(self) -> None:
        macros = parse_funcdefs("OLD(x) = [x]\nNEW(x) @ Weather and climate = [x] + 1\n")
        self.assertIsNone(macros["OLD"]["group"])
        self.assertEqual(macros["NEW"]["group"], "Weather and climate")
        with self.assertRaises(ValueError):
            parse_funcdefs("BAD(x) @ = [x]\n")

    def test_invalid_function_definition_reports_explicit_line_number(self) -> None:
        content = "\n" * 29 + "Cloud Base(Temperature; Dewpoint) = 0\n"
        with self.assertRaisesRegex(ValueError, r"regel 30"):
            parse_funcdefs(content, "Functies.funcdef")

    def test_english_sun_functions_match_dutch_aliases(self) -> None:
        data = pd.DataFrame({
            "time": ["2025-06-01 10:00", "2025-06-01 10:30", "2025-06-01 11:00"],
            "radiation": [200, 50, 150],
        })
        for english, dutch in (("STEP()", "STAP()"), ("SUNUP(52; 4; 1)", "ZONOP(52; 4; 1)"),
                               ("SUNDOWN(52; 4; 1)", "ZONONDER(52; 4; 1)")):
            with self.subTest(english=english):
                pd.testing.assert_series_equal(evaluate_formula(english, data, 0), evaluate_formula(dutch, data, 0))
        macros = load_funcdefs(Path(__file__).resolve().parents[1] / "src" / "Configs" / "Functiedefs")
        self.assertEqual(evaluate_formula("SunInterval([radiation])", data, 0, macros).tolist(), [0.5, 0, 0.5])
        self.assertTrue(evaluate_formula("DAYLENGTH()", data, 0, macros).between(0, 24).all())
        for name in ("STEP", "STAP", "SUNUP", "ZONOP", "SUNDOWN", "ZONONDER"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse_funcdefs(f"{name}() = 0")


class ColumnSearchTests(unittest.TestCase):
    def test_search_moves_to_exact_column_and_reports_missing_names(self) -> None:
        root = tk.Tk()
        root.geometry("360x250")
        try:
            columns = [f"Column {i}" for i in range(10)]
            table = ttk.Treeview(root, columns=columns, show="headings", height=3)
            table.pack(fill="both", expand=True)
            for column in columns:
                table.heading(column, text=column)
                table.column(column, width=120, stretch=False)
            item = table.insert("", "end", values=columns)
            found = []
            search = ColumnSearch(root, table, found.append)
            search.pack()
            root.update()
            search.entry.insert(0, "column 8")
            search._search()
            root.update()
            self.assertEqual(found, [8])
            self.assertGreater(table.xview()[0], 0)
            x, _, width, _ = table.bbox(item, "#9")
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + width, table.winfo_width())
            search.entry.delete(0, "end")
            search.entry.insert(0, "Column")
            with patch("viewer.messagebox.showinfo") as info:
                search._search()
                info.assert_called_once()
            self.assertEqual(found, [8])
            table.configure(columns=("Other",))
            search.entry.delete(0, "end")
            search.entry.insert(0, "Other")
            search._search()
            self.assertEqual(found, [8, 0])
        finally:
            root.destroy()

    def test_search_is_available_in_each_view_and_syncs_data_headers(self) -> None:
        import viewer

        data = pd.DataFrame({"time": ["2025-01-01", "2025-01-02"],
                             **{f"C{i}": [i, i + 1] for i in range(10)}})
        data.attrs["header_row_count"] = 0
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            save_formulas(base / "calc" / "UNIT.formuladef", [
                {"name": f"F{i}", "expression": f"[C{i}] * 2"} for i in range(10)
            ])
            save_pivots(base / "UNIT.pivotdef", [
                {"name": f"P{i}", "column": f"C{i}", "period": "day", "aggregation": "mean"}
                for i in range(10)
            ] + [{"name": "Weekly", "column": "C0", "period": "week", "aggregation": "mean"}])
            with patch.object(viewer, "PIVOT_DIR", base), \
                    patch.object(viewer, "PIVOT_FORMULA_DIR", base / "pivot_formulas"), \
                    patch.object(viewer, "FORMULA_DIR", base / "calc"), \
                    patch.object(viewer, "FUNCTION_DIR", base / "functions"), \
                    patch.object(viewer, "load_chart_config", return_value=[]), \
                    patch.object(viewer, "_fallback_charts", return_value=[]), \
                    patch.object(viewer.CampbellViewer, "_configure_window", lambda self: self.root.geometry("700x400")), \
                    patch.object(viewer.CampbellViewer, "_apply_window_icon", lambda self: None), \
                    patch.object(viewer.CampbellViewer, "_activate_window", lambda self: None):
                root = tk.Tk()
                try:
                    app = viewer.CampbellViewer(root, data, Path("UNIT.dat"), "UNIT")
                    for page_index, table, column, selection in (
                        (0, app.table, "C9", None),
                        (1, app.data2_table, "F9", app.data2_selection),
                        (2, app.pivot_table, "P9", app.pivot_selection),
                    ):
                        app.notebook.select(app.notebook.tabs()[page_index])
                        root.update()
                        page = root.nametowidget(app.notebook.tabs()[page_index])
                        search = next(child for frame in page.winfo_children()
                                      for child in frame.winfo_children() if isinstance(child, ColumnSearch))
                        search.entry.insert(0, column)
                        search._search()
                        root.update()
                        self.assertGreater(table.xview()[0], 0)
                        if selection is None:
                            self.assertEqual(app._marked_column_index, 10)
                            self.assertAlmostEqual(table.xview()[0], app.frozen_table.xview()[0], places=2)
                        else:
                            self.assertEqual(selection.cursor[1], "#11")
                    app.pivot_choice.current(app.pivot_periods.index("week"))
                    app._change_pivot_period()
                    with patch("viewer.messagebox.showinfo") as info:
                        search._search()
                        info.assert_called_once()
                finally:
                    root.destroy()
