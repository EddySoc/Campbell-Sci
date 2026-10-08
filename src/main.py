from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

import pandas as pd

from settings import get_last_dir, set_last_dir
from viewer import CampbellViewer, logger_from_filename, prepare_external_configs, read_dat_file


def set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return

    set_app_id = ctypes.WinDLL("shell32", use_last_error=True).SetCurrentProcessExplicitAppUserModelID
    set_app_id.argtypes = [ctypes.c_wchar_p]
    set_app_id.restype = ctypes.c_long
    result = set_app_id("EddySoc.CampbellSciViewer.2")
    if result < 0:
        raise OSError(f"Windows kon de app-identiteit niet instellen (HRESULT 0x{result & 0xFFFFFFFF:08X})")


def choose_dat_file(parent: tk.Misc | None = None) -> str:
    return filedialog.askopenfilename(
        parent=parent,
        title="Kies een Campbell .dat-bestand",
        filetypes=[("Campbell data", "*.dat"), ("Alle bestanden", "*.*")],
        initialdir=get_last_dir(),
    )


def main() -> None:
    set_windows_app_user_model_id()

    try:
        prepare_external_configs()
    except OSError as exc:
        messagebox.showerror(
            "Campbell Sci",
            f"De configuratiemap naast het programma kon niet worden aangemaakt:\n{exc}",
        )
        return

    chooser = tk.Tk()
    chooser.withdraw()
    file_path = choose_dat_file(chooser)
    chooser.destroy()
    if not file_path:
        return

    try:
        source = Path(file_path)
        set_last_dir(source.parent)
        logger = logger_from_filename(source)
        data = read_dat_file(source)
        if logger in {"BM", "BRAS"}:
            data = data.replace(["6999", "-6999", "7999", "-7999", "NAN", "INF"], float("nan"))
            if "IR_R_R_Avg" in data.columns:
                infrared = pd.to_numeric(data["IR_R_R_Avg"], errors="coerce")
                data["Leaf T."] = (infrared / 0.0000000567) ** 0.25 - 273.16
        root = tk.Tk()
        CampbellViewer(root, data, source, logger)
        root.mainloop()
    except Exception as exc:
        error_root = tk.Tk()
        error_root.withdraw()
        messagebox.showerror("Campbell Sci", str(exc), parent=error_root)
        error_root.destroy()


if __name__ == "__main__":
    main()
