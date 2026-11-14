from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from formula_editor import FormulaEditor
from formulas import FormulaError, apply_formulas, evaluate_formula, function_help, load_formulas, load_funcdefs
from function_catalog import CATALOG_NAME, include_unlisted, load_catalog, save_catalog
from pivots import calculate_pivots, load_pivots

CONFIG = Path(__file__).resolve().parents[1] / "src" / "Configs"


class FormulaMigrationTests(unittest.TestCase):
    def test_dew_point_uses_temperature_and_relative_humidity(self) -> None:
        macros = load_funcdefs(CONFIG / "Functiedefs")
        data = pd.DataFrame({
            "time": ["unit", "processing", "2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"],
            "temperature": ["C", "Avg", 20, 20, 20, None],
            "humidity": ["%", "Avg", 50, 100, 0, 50],
        })
        values = evaluate_formula("DauwPunt([temperature]; [humidity])", data, 2, macros)
        self.assertAlmostEqual(values.iloc[0], 9.25, places=2)
        self.assertAlmostEqual(values.iloc[1], 20, places=6)
        self.assertTrue(pd.isna(values.iloc[2]))
        self.assertTrue(pd.isna(values.iloc[3]))

    def test_sunshine_is_summed_per_day_in_pivot(self) -> None:
        data = pd.DataFrame({
            "TIMESTAMP": ["units", "processing", "2025-06-01 10:00", "2025-06-01 10:30",
                          "2025-06-02 10:00", "2025-06-02 10:30"],
            "Solar_Rad_Avg": ["W/m2", "Avg", 200, 50, 150, 300],
            **{name: ["units", "Avg", 1, 2, 3, 4] for name in (
                "Temp_02_m_Avg", "Temp_24_m_Avg", "IR_R_R_Avg", "IR_R_I_Avg",
                "Temp_40_m_Avg", "R_H_02_m_Avg", "R_H_24_m_Avg", "R_H_40_m_Avg",
                "T_Fall_1_Tot", "T_Fall_2_Tot", "Wind_40_m", "lfWet(1)", "lfWet(2)", "lfWet(3)"
            )},
        })
        macros = load_funcdefs(CONFIG / "Functiedefs")
        formulas = load_formulas("BM", None, CONFIG / "Formuladefs")
        calculated, _, errors = apply_formulas(data, formulas, 2, macros)
        self.assertEqual(errors, [])
        self.assertEqual(calculated["Zoninterval"].iloc[2:].tolist(), [0.5, 0.0, 0.5, 0.5])
        definitions = load_pivots(CONFIG / "Pivotdefs" / "BM.pivotdef")
        day = calculate_pivots(calculated, 2, definitions)["day"]
        self.assertEqual(day["Uren_Zon"].tolist(), [0.5, 1.0])
        pivot_formulas = load_formulas("BM", None, CONFIG / "PivotFormuladefs" / "day")
        result, _, errors = apply_formulas(day, pivot_formulas, 0, macros)
        self.assertEqual(errors, [])
        self.assertTrue(result["Zonpercentage"].between(0, 100).all())

    def test_missing_sunshine_input_does_not_remove_other_pivots(self) -> None:
        import tkinter as tk
        import viewer

        data = pd.DataFrame({
            "TIMESTAMP": ["2025-06-01 10:00", "2025-06-01 10:30"],
            "Temp_02_m_Avg": [10, 12], "lfWet(1)": [1, 2],
            "lfWet(2)": [1, 2], "lfWet(3)": [1, 2],
        })
        root = tk.Tk()
        root.withdraw()
        try:
            app = viewer.CampbellViewer.__new__(viewer.CampbellViewer)
            app.root, app.chart_data, app.header_row_count = root, data, 0
            app.logger, app.location = "BM", None
            app._compute_pivots()
            self.assertEqual(app.pivot_frames["day"]["Temp Day Min"].tolist(), [10])
            self.assertNotIn("Uren_Zon", app.pivot_frames["day"])
            with patch("viewer.messagebox.showwarning") as warning:
                root.update()
                root.after(300, root.quit)
                root.mainloop()
                warning.assert_called_once()
                self.assertIn("Zoninterval", warning.call_args.args[1])
        finally:
            root.destroy()

    def test_leaf_temperature_and_removed_daily_functions(self) -> None:
        macros = load_funcdefs(CONFIG / "Functiedefs")
        data = pd.DataFrame({"time": ["2025-01-01", "2025-01-02"],
                             "radiation": [400.0, -1.0]})
        values = evaluate_formula("LEAFT([radiation])", data, 0, macros)
        self.assertAlmostEqual(values.iloc[0], (400 / 0.0000000567) ** 0.25 - 273.15)
        self.assertTrue(pd.isna(values.iloc[1]))
        for name in ("DAGMIN", "DAGMAX", "DAGSOM", "DAGGEM", "DAYMIN", "DAYMAX",
                     "DAYSUM", "DAYAVG", "DAYAVERAGE"):
            with self.subTest(name=name), self.assertRaises(FormulaError):
                evaluate_formula(f"{name}([radiation])", data, 0, macros)


class FunctionCatalogTests(unittest.TestCase):
    def test_catalog_accepts_named_colors_and_rejects_unknown_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / CATALOG_NAME
            path.write_text(json.dumps({"groups": [
                {"name": "Yellow group", "color": "yellow", "functions": []},
                {"name": "Blue group", "color": "lightblue", "functions": []},
            ]}), encoding="utf-8")
            self.assertEqual([group.color for group in load_catalog(path)], ["yellow", "lightblue"])

            content = json.loads(path.read_text(encoding="utf-8"))
            content["groups"][0]["color"] = "not-a-color"
            path.write_text(json.dumps(content), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_catalog(path)

    def test_catalog_roundtrip_and_validation(self) -> None:
        groups = load_catalog(CONFIG / "Functiedefs" / CATALOG_NAME)
        meteo = next(group for group in groups if group.name == "Meteo")
        self.assertIn("LEAFT", [entry.name for entry in meteo.functions])
        self.assertEqual(
            {name.upper() for name in function_help()} | set(load_funcdefs(CONFIG / "Functiedefs")),
            {entry.name.upper() for group in groups for entry in group.functions},
        )
        include_unlisted(groups, ["NEWFUNCTION"])
        self.assertEqual(groups[-1].functions[-1].name, "NEWFUNCTION")
        groups[0].functions[0].visible = False
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / CATALOG_NAME
            save_catalog(path, groups)
            self.assertEqual(load_catalog(path), groups)
            content = json.loads(path.read_text(encoding="utf-8"))
            content["groups"][0]["functions"].append({"name": "sum", "visible": True})
            path.write_text(json.dumps(content), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_catalog(path)

    def test_editor_selection_is_saved_and_group_headers_are_not_inserted(self) -> None:
        import tkinter as tk

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / CATALOG_NAME).write_text(
                (CONFIG / "Functiedefs" / CATALOG_NAME).read_text(encoding="utf-8"), encoding="utf-8"
            )
            (base / "Functies.funcdef").write_text("LEAFT(x) = [x] * 2\n", encoding="utf-8")
            root = tk.Tk()
            root.withdraw()
            try:
                editor = FormulaEditor(root, pd.DataFrame({"time": [], "value": []}), 0,
                                       "UNIT", base, function_dir=base)
                self.assertIsNone(editor.function_names[0])
                editor.functions_box.selection_set(0)
                with patch.object(editor, "_insert_text") as insert:
                    editor._insert_function(None)
                    insert.assert_not_called()
                editor._select_functions()
                window = next(child for child in editor.winfo_children() if isinstance(child, tk.Toplevel))
                frame = next(child for child in window.winfo_children() if isinstance(child, tk.Frame) or child.winfo_class() == "TFrame")
                box = next(child for child in frame.winfo_children() if isinstance(child, tk.Listbox))
                index = next(i for i, text in enumerate(box.get(0, "end")) if text.startswith("Meteo: LEAFT"))
                box.selection_clear(index)
                save_button = next(child for child in window.winfo_children() if child.winfo_class() == "TButton")
                save_button.invoke()
                self.assertNotIn("LEAFT", editor.function_names)
                self.assertFalse(next(entry.visible for group in load_catalog(base / CATALOG_NAME)
                                      for entry in group.functions if entry.name == "LEAFT"))
            finally:
                root.destroy()
