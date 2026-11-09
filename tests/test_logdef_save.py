from __future__ import annotations

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import viewer
from logdef_editor import LogdefEditor


class LogdefSaveTests(unittest.TestCase):
    def test_new_and_save_as_invoke_callback_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            path = config / "TEST.logdef"
            copy = config / "OTHER.logdef"
            root = tk.Tk()
            saved: list[Path] = []
            try:
                editor = LogdefEditor(root, ["time", "value"], "TEST", config, on_saved=saved.append)
                editor.selected.insert("end", "value")
                with patch("logdef_editor.filedialog.asksaveasfilename", return_value=str(path)):
                    editor._save()
                self.assertEqual(saved, [path])
                self.assertFalse(editor.winfo_exists())
                editor = LogdefEditor(root, ["time", "value"], "TEST", config, on_saved=saved.append)
                with patch("logdef_editor.filedialog.asksaveasfilename", return_value=str(copy)) as dialog:
                    editor._save(save_as=True)
                dialog.assert_called_once()
                self.assertEqual(saved, [path, copy])
                self.assertTrue(copy.is_file())
                self.assertFalse(editor.winfo_exists())
            finally:
                root.update_idletasks()
                root.destroy()

    def test_existing_save_refreshes_viewer_and_preserves_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            path = config / "TEST.logdef"
            original = "Old;line;time,value;;;0.0;\n"
            path.write_text(original, encoding="utf-8")
            data = pd.DataFrame({"time": ["2025-01-01", "2025-01-02"], "value": [1, 3]})
            data.attrs["header_row_count"] = 0
            with patch.object(viewer, "LOGDEF_DIR", config), \
                 patch.object(viewer, "FORMULA_DIR", config / "formulas"), \
                 patch.object(viewer, "PIVOT_DIR", config / "pivots"), \
                 patch.object(viewer, "PIVOT_FORMULA_DIR", config / "pivot_formulas"), \
                 patch.object(viewer, "FUNCTION_DIR", config / "functions"), \
                 patch.object(viewer.CampbellViewer, "_configure_window", lambda self: self.root.geometry("600x400")), \
                 patch.object(viewer.CampbellViewer, "_apply_window_icon", lambda self: None), \
                 patch.object(viewer.CampbellViewer, "_activate_window", lambda self: None):
                root = tk.Tk()
                try:
                    app = viewer.CampbellViewer(root, data, Path("TEST.dat"), "TEST")
                    root.update()
                    app.data.loc[0, "value"] = 8
                    app._data_changed = True
                    editor = LogdefEditor(root, list(data.columns), "TEST", config, on_saved=app._reload_logdef)
                    editor.name_var.set("Updated")
                    with patch("logdef_editor.filedialog.asksaveasfilename") as dialog, \
                         patch("logdef_editor.messagebox.showinfo") as info:
                        editor._save()
                    root.update()
                    dialog.assert_not_called()
                    info.assert_not_called()
                    self.assertFalse(editor.winfo_exists())
                    self.assertEqual(path.with_name(path.name + ".bak").read_text(encoding="utf-8"), original)
                    self.assertEqual(app.notebook.tab(app.notebook.tabs()[3], "text"), "Updated")
                    self.assertEqual(app.chart_axes[0][1][0].get_lines()[0].get_ydata().tolist(), [8, 3])
                    self.assertFalse(app._data_changed)
                finally:
                    root.destroy()

    def test_cancel_and_write_error_keep_editor_open(self) -> None:
        data = ["time", "value"]
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            try:
                editor = LogdefEditor(root, data, "TEST", Path(directory))
                editor.selected.insert("end", "value")
                with patch("logdef_editor.filedialog.asksaveasfilename", return_value=""), \
                     patch("logdef_editor.messagebox.showerror") as error:
                    editor._save()
                error.assert_not_called()
                self.assertTrue(editor.winfo_exists())
                editor.loaded_path = Path(directory) / "TEST.logdef"
                with patch.object(Path, "write_text", side_effect=OSError("disk error")), \
                     patch("logdef_editor.messagebox.showerror") as error:
                    editor._save()
                error.assert_called_once()
                self.assertTrue(editor.winfo_exists())
            finally:
                root.update_idletasks()
                root.destroy()

    def test_inactive_save_as_warns_instead_of_claiming_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            (config / "TEST.logdef").write_text("Chart;line;time,value;;;0.0;\n", encoding="utf-8")
            root = tk.Tk()
            try:
                app = viewer.CampbellViewer.__new__(viewer.CampbellViewer)
                app.root, app.logger, app.location = root, "TEST", None
                with patch.object(viewer, "LOGDEF_DIR", config), \
                     patch.object(app, "_reload_formulas") as refresh, \
                     patch("viewer.messagebox.showwarning") as warning:
                    app._reload_logdef(config / "OTHER.logdef")
                warning.assert_called_once()
                refresh.assert_not_called()
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
