from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
from typing import cast

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

try:
    import win32com.client
except Exception:  # pragma: no cover
    win32com = None

from campbell_sci.parser import detect_columns, parse_filename, read_dat_file
from campbell_sci.graph_builder import build_configured_charts, build_fallback_charts


def _delete_quietly(path: str | Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _get_excel_application():
    """Reuse a running Excel instance, including the VSTO instance started by Visual Studio."""
    try:
        return win32com.client.GetActiveObject("Excel.Application"), False
    except Exception:
        return win32com.client.Dispatch("Excel.Application"), True


def open_generated_file(path: str | Path):
    """Show the workbook in Excel, then delete it once the user closes it.

    The .xlsx is only a temporary viewer for the .dat data, so nothing should
    remain on disk after the Excel window is closed.
    """
    path_str = str(path)
    try:
        if win32com is not None:
            excel, started_by_campbell = _get_excel_application()
            excel.Visible = True
            excel.DisplayAlerts = False
            workbook = excel.Workbooks.Open(path_str)
            excel.WindowState = -4137  # xlMaximized
            excel.DisplayFullScreen = False
            workbook.Activate()
            print(f"Excel geopend: {path_str}")

            # Block until the user closes the workbook (or Excel itself).
            try:
                while True:
                    time.sleep(1)
                    try:
                        workbook.Name  # raises once the workbook is closed
                    except Exception:
                        break
            except KeyboardInterrupt:
                print("Wachten op Excel onderbroken; Excel en de werkmap blijven geopend.")
                return

            if started_by_campbell:
                try:
                    excel.Quit()
                except Exception:
                    pass

            _delete_quietly(path)
            return
    except Exception as exc:
        print(f"Excel COM-opening mislukt; standaardopening wordt gebruikt: {exc}")

    try:
        os.startfile(path_str)
        print(f"Excel geopend: {path_str}")
        print("Let op: sluit Excel manueel; het tijdelijke .xlsx-bestand kan dan niet automatisch verwijderd worden.")
    except Exception as exc:
        print(f"Excel niet automatisch geopend; bestand is klaar: {path_str}")
        print(f"Details: {exc}")


def build_output_path(file_name: str | Path) -> Path:
    """Create a unique output name in a temp folder; the .dat file stays the only real artifact."""
    base = Path(file_name)
    temp_dir = Path(tempfile.gettempdir()) / "campbell_sci"
    temp_dir.mkdir(parents=True, exist_ok=True)

    candidate = temp_dir / f"{base.stem}.xlsx"
    counter = 1

    while candidate.exists():
        candidate = temp_dir / f"{base.stem}_{counter}.xlsx"
        counter += 1

    return candidate


def sanitize_sheet_name(name):
    text = str(name).replace("/", "_").replace("\\", "_")
    text = text.strip()
    return text[:31] if text else "Series"


def _to_number_if_possible(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value

    text = str(value).strip()
    if text == "":
        return None

    # Support both decimal dots and decimal commas from logger exports.
    normalized = text.replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return value


def _first_measurement_row_index(df) -> int:
    time_col = df.columns[0]
    for idx, value in enumerate(df[time_col].tolist()):
        if pd.notna(pd.to_datetime(value, errors="coerce")):
            return idx
    return 0


def _write_data_sheet(wb: Workbook, df) -> Worksheet:
    """Write the raw Data sheet (headers + measurement rows) shared by every processor."""
    data_sheet = cast(Worksheet, wb.active)
    data_sheet.title = "Data"

    # Row 1: all column headers from the TOA5 export.
    data_sheet.append(list(df.columns))

    first_data_idx = _first_measurement_row_index(df)

    # Keep logger metadata rows intact; convert only measurement rows to numerics.
    for row_idx, row in df.iterrows():
        row_values = list(row.tolist())
        if row_idx >= first_data_idx:
            row_values = [row_values[0]] + [_to_number_if_possible(v) for v in row_values[1:]]
        data_sheet.append(row_values)

    return data_sheet


def create_workbook_for_file(df, file_name: str, label: str):
    """Create one workbook per file with a raw Data sheet and one chart sheet per column."""
    output_path = build_output_path(file_name)
    wb = Workbook()

    data_sheet = _write_data_sheet(wb, df)
    first_data_idx = _first_measurement_row_index(df)
    first_data_excel_row = first_data_idx + 2

    build_fallback_charts(wb, data_sheet.title, first_data_excel_row)

    wb.save(output_path)
    print(f"Werkmap opgeslagen: {output_path}")
    open_generated_file(output_path)
    return output_path


def process_bm(df, file_name: str):
    """Python-versie van `MDataLogger.METEO`: grafieken uit de BM-configuratie."""
    from campbell_sci.logger_routines import add_leaf_temperature, mask_bad_values

    df = mask_bad_values(df)
    df = add_leaf_temperature(df)

    output_path = build_output_path(file_name)
    wb = Workbook()
    data_sheet = _write_data_sheet(wb, df)
    first_data_row = _first_measurement_row_index(df) + 2

    build_configured_charts(wb, "BM", data_sheet.title, first_data_row=first_data_row)

    wb.save(output_path)
    print(f"Werkmap opgeslagen: {output_path}")
    open_generated_file(output_path)


def process_tdr(df, file_name: str):
    """Single workbook with one sheet per measurement series for TDR files."""
    create_workbook_for_file(df, file_name, "TDR")


def process_default(df, file_name: str):
    """Fallback processor: one workbook with one chart sheet per measurement column."""
    create_workbook_for_file(df, file_name, "Fallback")


# Processors die op de volledige DataFrame werken (geen detect_columns-inkrimping).
FULL_DATAFRAME_PROCESSORS = (process_default, process_bm)


def select_processor(logger_name: str, date_string: str | None = None):
    """Return a processing function based on the datalogger name.

    For now, unknown logger names stay on the generic fallback so the full raw
    Campbell export is preserved in the Data sheet and the first custom routes are
    added only when truly needed.
    """
    mapping = {
        "BM": process_bm,
        "BRAS": process_bm,
        "TDR": process_tdr,
    }
    return mapping.get(logger_name, process_default)


def process_file(file_path: str | Path):
    """Main entry point for a single datalogger file."""
    path = Path(file_path)
    meta = parse_filename(path.name)
    df = read_dat_file(path)

    processor = select_processor(meta["logger"], meta["date_string"])
    if processor in FULL_DATAFRAME_PROCESSORS:
        processor(df, path.stem)
        return {
            "logger": meta["logger"],
            "date_string": meta["date_string"],
            "date_token": meta["date_token"],
            "data": df,
        }

    df_clean, _ = detect_columns(df)
    processor(df_clean, path.stem)

    return {
        "logger": meta["logger"],
        "date_string": meta["date_string"],
        "date_token": meta["date_token"],
        "data": df_clean,
    }


def open_file_dialog() -> str:
    """Open a file dialog and return selected path."""
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="Kies een .dat of .csv bestand",
        filetypes=[("Data files", "*.dat *.csv"), ("All files", "*.*")],
    )
    root.destroy()
    return file_path


def main():
    """Run the app with a simple open-dialog workflow."""
    file_path = open_file_dialog()
    if not file_path:
        print("Geen bestand geselecteerd.")
        return

    print(f"Geselecteerd bestand: {file_path}")
    result = process_file(file_path)
    print(f"Logger: {result['logger']}")
    print(f"Datum/tijd: {result['date_string']}")
    print(f"Aantal rijen: {len(result['data'])}")
    print(f"Aantal kolommen: {len(result['data'].columns)}")


if __name__ == "__main__":
    main()
