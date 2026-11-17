from __future__ import annotations

import csv
import math
from pathlib import Path
import re
import sys
from tkinter import ttk
from typing import Any, Callable

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import pandas as pd
from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError
from tkinter import font as tkfont
from formulas import apply_formulas, load_formulas, load_funcdefs
from app_config import prepare_external_configs
from logdef_format import parse_chart_fields
from pivots import PERIODS, calculate_pivots, load_pivots, period_start, pivot_path
from i18n import available_languages, current_language, init_language, set_language as apply_language, t
from settings import get_auto_scale_y, get_last_dir, set_auto_scale_y, set_language, set_last_dir
import logging
import tkinter as tk
from tkinter import filedialog, messagebox

PAGE_SIZE = 1000
HEADER_ROWS = 2
LINE_COLORS = ["#1F4E78", "#C00000", "#548235", "#BF8F00", "#7030A0", "#2E75B6", "#833C00"]
_APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CONFIGS_DIR = _APP_DIR / "Configs"
LOGDEF_DIR = CONFIGS_DIR / "Logdefs"
MENU_DIR = CONFIGS_DIR / "Menudefs"
FORMULA_DIR = CONFIGS_DIR / "Formuladefs"
FUNCTION_DIR = CONFIGS_DIR / "Functiedefs"
HELPDEF_DIR = CONFIGS_DIR / "Helpdefs"
PIVOT_DIR = CONFIGS_DIR / "Pivotdefs"
PIVOT_FORMULA_DIR = CONFIGS_DIR / "PivotFormuladefs"
HELPDEF_PATH = HELPDEF_DIR / "Campbell_Sci.helpdef"
HELP_IMAGE_PATH = HELPDEF_DIR / "Meettoren.jpg"


def helpdef_path() -> Path:
    localized = HELPDEF_DIR / f"Campbell_Sci.{current_language()}.helpdef"
    return localized if localized.is_file() else HELPDEF_PATH


def application_icon_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(__file__).with_name("CampBell Icon.ico")
    return Path(__file__).resolve().parent.parent / "screenshots" / "CampBell Icon.ico"


def _read_generic_csv(path: Path, lines: list[str]) -> pd.DataFrame:
    """Leest een gewone csv/txt-meetfile: eerste regel = kolomnamen, tijdkolom wordt vooraan gezet."""
    from io import StringIO

    lines = [line for line in lines if line.strip()]
    if len(lines) < 2:
        raise ValueError(t("err.no_data", name=path.name))
    sample = "\n".join(lines[:50])
    counts = {sep: lines[0].count(sep) for sep in (";", "\t", ",", "|")}
    delimiter = max(counts, key=counts.get)
    if counts[delimiter] == 0:
        raise ValueError(t("err.no_toa5_header", name=path.name))
    decimal = "," if delimiter != "," and re.search(r"\d,\d", sample) else "."
    try:
        data = pd.read_csv(
            StringIO("\n".join(lines)), sep=delimiter, decimal=decimal, engine="python",
            on_bad_lines="skip", header=0, na_values=["NAN", "NaN", "nan", "-", ""],
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ValueError(t("err.no_data", name=path.name)) from exc
    if data.empty:
        raise ValueError(t("err.no_data", name=path.name))
    data.columns = [str(column).strip() or f"Column{index + 1}" for index, column in enumerate(data.columns)]

    for column in data.columns:
        if pd.api.types.is_numeric_dtype(data[column]):
            continue
        text = data[column].astype(str)
        dayfirst = bool(text.str.match(r"^\s*\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}").mean() > 0.5)
        stamps = pd.to_datetime(text, format="mixed", dayfirst=dayfirst, errors="coerce")
        if stamps.notna().mean() >= 0.5:
            data[column] = stamps.dt.strftime("%Y-%m-%d %H:%M:%S")
            data = data[[column] + [c for c in data.columns if c != column]]
            break
    data.attrs["header_row_count"] = 0
    data.attrs["generic_csv"] = delimiter
    return data


def read_dat_file(file_path: str | Path) -> pd.DataFrame:
    path = Path(file_path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    toa5_header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip().upper().startswith('"TIMESTAMP"')
            or line.strip().upper().startswith("TIMESTAMP")
        ),
        None,
    )
    toaci1_header_index = None
    if lines and lines[0].lstrip().upper().startswith('"TOACI1"'):
        toaci1_header_index = next(
            (
                index
                for index, line in enumerate(lines[1:], 1)
                if line.strip().upper().startswith('"TMSTAMP"')
                or line.strip().upper().startswith("TMSTAMP")
            ),
            None,
        )
    header_index = toa5_header_index if toa5_header_index is not None else toaci1_header_index
    if header_index is None:
        return _read_generic_csv(path, lines)
    from io import StringIO
    data = pd.read_csv(
        StringIO("\n".join(lines[header_index:])),
        sep=",",
        quotechar='"',
        engine="python",
        on_bad_lines="skip",
        header=0,
    )
    if data.empty:
        raise ValueError(t("err.no_data", name=path.name))
    data.columns = [str(column).strip() for column in data.columns]
    data.attrs["header_row_count"] = HEADER_ROWS if toa5_header_index is not None else 0
    return data


def merge_columns_by_time(
    base: pd.DataFrame, base_headers: int, extra: pd.DataFrame, extra_headers: int,
    columns: list[str], prefix: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Voegt kolommen uit `extra` toe aan `base`, gekoppeld op exact gelijke tijdstempels."""
    base_times = pd.to_datetime(base.iloc[base_headers:, 0], format="mixed", errors="coerce")
    extra_times = pd.to_datetime(extra.iloc[extra_headers:, 0], format="mixed", errors="coerce")
    result = base.copy()
    added: list[str] = []
    existing = {str(c).casefold() for c in result.columns}
    keep = extra_times.notna().to_numpy()
    if not base_times.dropna().isin(extra_times.dropna()).any():
        raise ValueError(t("err.no_timestamp_match"))
    for column in columns:
        name = column if column.casefold() not in existing else f"{prefix}_{column}"
        suffix = 2
        unique = name
        while unique.casefold() in existing:
            unique = f"{name}_{suffix}"
            suffix += 1
        values = pd.Series(extra.iloc[extra_headers:][column].to_numpy()[keep], index=extra_times[keep].to_numpy())
        values = values[~values.index.duplicated(keep="last")]
        matched = values.reindex(base_times.to_numpy()).to_numpy()
        header_part = [
            extra.iloc[row][column] if row < extra_headers else "" for row in range(base_headers)
        ]
        new_column = np.empty(len(base), dtype=object)
        new_column[:base_headers] = header_part
        new_column[base_headers:] = matched
        result[unique] = new_column
        existing.add(unique.casefold())
        added.append(unique)
    result.attrs.update(base.attrs)
    return result, added


def _format_toa5_cell(value: Any, na_text: str) -> Any:
    if pd.isna(value):
        return na_text
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _read_preamble_lines(file_path: str | Path) -> list[str]:
    path = Path(file_path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if lines and lines[0].lstrip().upper().startswith('"TOACI1"'):
        return lines[:1]
    header_index = next(
        (index for index, line in enumerate(lines) if line.strip().upper().startswith('"TIMESTAMP"') or line.strip().upper().startswith("TIMESTAMP")),
        None,
    )
    return lines[:header_index] if header_index else []


def chart_config_path(
    logger: str,
    config_dir: str | Path = LOGDEF_DIR,
    location: str | None = None,
) -> Path | None:
    config_path = Path(config_dir)
    candidates = []
    if location:
        candidates.append(config_path / f"{logger.upper()}_{location.upper()}.logdef")
    candidates.append(config_path / f"{logger.upper()}.logdef")
    if logger.upper() == "BRAS":
        candidates.append(config_path / "BM.logdef")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def load_chart_config(
    logger: str,
    config_dir: str | Path = LOGDEF_DIR,
    location: str | None = None,
) -> list[dict[str, Any]]:
    path = chart_config_path(logger, config_dir, location)
    if path is None:
        return []
    charts = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = parse_chart_fields(line, path.name, line_number)
        name, chart_type, columns, minimum, maximum, number_format, tab_color = fields[:7]
        charts.append({
            "name": name,
            "type": chart_type.lower(),
            "columns": [column.strip() for column in columns.split(",") if column.strip()],
            "minimum": float(minimum) if minimum else None,
            "maximum": float(maximum) if maximum else None,
            "number_format": number_format or "0.0",
            "tab_color": tab_color,
            "trend": fields[7] if len(fields) == 8 else "",
        })
    return charts


def load_menu_config(name: str = "graph", menu_dir: str | Path = MENU_DIR) -> list[dict[str, str]]:
    path = Path(menu_dir) / f"{name}.menudef"
    if not path.is_file():
        return []
    items = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split(";", 1)]
        if len(fields) != 2 or not all(fields):
            raise ValueError(t("err.invalid_menudef_line", name=path.name, line=line_number))
        label, action = fields
        items.append({"label": t(f"menu.{action}", default=label), "action": action})
    return items


def _header_lookup(data: pd.DataFrame) -> dict[str, str]:
    headers = {str(column).strip().upper(): str(column) for column in data.columns}
    if "TMSTAMP" in headers:
        headers.setdefault("TIMESTAMP", headers["TMSTAMP"])
    return headers


def _resolve_header(headers: dict[str, str], configured: str) -> tuple[str, bool]:
    secondary = configured.lower().endswith("@secondary")
    name = configured[:-10].strip() if secondary else configured.strip()
    column = headers.get(name.upper())
    if column is None:
        raise ValueError(t("err.column_not_in_data", name=name))
    return column, secondary


def _fallback_charts(data: pd.DataFrame) -> list[dict[str, Any]]:
    if data.empty:
        return []
    charts = []
    time_column = str(data.columns[0])
    for column in data.columns[1:]:
        values = pd.to_numeric(data[column], errors="coerce")
        if values.notna().any():
            charts.append({
                "name": str(column)[:31],
                "type": "line",
                "columns": [time_column, str(column)],
                "minimum": None,
                "maximum": None,
                "number_format": "0.0",
                "tab_color": "",
            })
    return charts


def logger_and_location_from_filename(path: Path) -> tuple[str, str | None]:
    parts = path.stem.split("_")
    logger = parts[0].strip()
    if not logger:
        raise ValueError(t("err.no_logger_name", name=path.name))
    location = parts[1].strip() if len(parts) > 1 else ""
    if not location or re.fullmatch(r"\d{4}(?:-?\d{2}){0,2}", location):
        location = None
    return logger.upper(), location.upper() if location else None


def logger_from_filename(path: Path) -> str:
    return logger_and_location_from_filename(path)[0]


def _x_values(series: pd.Series) -> tuple[pd.Series, bool]:
    dates = pd.to_datetime(series, format="mixed", errors="coerce")
    if dates.notna().sum() >= max(2, int(len(series) * 0.5)):
        return dates, True
    numbers = pd.to_numeric(series, errors="coerce")
    if numbers.notna().sum() >= max(2, int(len(series) * 0.5)):
        return numbers, False
    return pd.Series(range(len(series)), index=series.index), False


class ColumnSearch(ttk.Frame):
    def __init__(self, parent: tk.Misc, table: ttk.Treeview, on_found: Callable[[int], None] | None = None):
        super().__init__(parent)
        self.table = table
        self.on_found = on_found
        ttk.Label(self, text=t("column_search.label")).pack(side="left", padx=(8, 4))
        self.entry = ttk.Entry(self, width=22)
        self.entry.pack(side="left")
        self.entry.bind("<Return>", self._search)
        ttk.Button(self, text=t("column_search.go"), command=self._search).pack(side="left", padx=4)

    def _search(self, _event: object = None) -> str:
        query = self.entry.get().strip()
        columns = [str(column) for column in self.table["columns"]]
        index = next((i for i, name in enumerate(columns) if name == query), None)
        if index is None:
            index = next((i for i, name in enumerate(columns) if name.casefold() == query.casefold()), None)
        if not query or index is None:
            messagebox.showinfo(t("column_search.label"), t("column_search.not_found", name=query), parent=self)
            return "break"
        self.table.update_idletasks()
        widths = [int(self.table.column(column, "width")) for column in columns]
        total = sum(widths)
        if total:
            left = sum(widths[:index]) + widths[index] / 2 - self.table.winfo_width() / 2
            self.table.xview_moveto(max(0.0, left / total))
        if self.on_found:
            self.on_found(index)
        return "break"


class ReadOnlyCellSelection:
    """Excel-achtige celselectie (muis, Shift, pijltjes, Ctrl+C) op een Treeview."""

    def __init__(self, table: ttk.Treeview, on_context_menu: Any = None):
        self.table = table
        self.cursor: tuple[str, str] | None = None
        self.anchor: tuple[str, str] | None = None
        self.labels: list[tk.Label] = []
        self.on_context_menu = on_context_menu
        table.bind("<Button-1>", self._click)
        table.bind("<B1-Motion>", self._drag)
        table.bind("<Key>", self._key)
        table.bind("<Control-c>", self._copy)
        table.bind("<Configure>", lambda _event: self.refresh())
        self._yscroll = table.cget("yscrollcommand")
        self._xscroll = table.cget("xscrollcommand")
        table.configure(
            yscrollcommand=lambda *args: (table.tk.call(self._yscroll, *args) if self._yscroll else None, self.refresh()),
            xscrollcommand=lambda *args: (table.tk.call(self._xscroll, *args) if self._xscroll else None, self.refresh()),
        )

    def _cell_at(self, event: tk.Event) -> tuple[str, str] | None:
        x = event.x_root - self.table.winfo_rootx()
        y = event.y_root - self.table.winfo_rooty()
        item = self.table.identify_row(y)
        column_id = self.table.identify_column(x)
        return (item, column_id) if item and column_id else None

    def _click(self, event: tk.Event) -> str:
        self.table.focus_set()
        cell = self._cell_at(event)
        if cell:
            self.set_cursor(*cell, extend=bool(event.state & 0x1))
        return "break"

    def _drag(self, event: tk.Event) -> str:
        cell = self._cell_at(event)
        if cell and cell != self.cursor:
            self.set_cursor(*cell, extend=True)
        return "break"

    def set_cursor(self, item: str | None, column_id: str | None = None, extend: bool = False) -> None:
        self.cursor = (item, column_id) if item and column_id else None
        if not extend or self.anchor is None or self.cursor is None:
            self.anchor = self.cursor
        self.refresh()

    def bounds(self) -> tuple[int, int, int, int] | None:
        if self.cursor is None or self.anchor is None:
            return None
        rows = sorted((self.table.index(self.anchor[0]), self.table.index(self.cursor[0])))
        cols = sorted((int(self.anchor[1][1:]) - 1, int(self.cursor[1][1:]) - 1))
        return rows[0], rows[1], cols[0], cols[1]

    def refresh(self) -> None:
        bounds = self.bounds()
        cells: list[tuple[str, str, bool]] = []
        if bounds:
            row1, row2, col1, col2 = bounds
            seen_visible = False
            for item in self.table.get_children()[row1 : row2 + 1]:
                if not self.table.bbox(item):
                    if seen_visible:
                        break
                    continue
                seen_visible = True
                for col in range(col1, col2 + 1):
                    column_id = f"#{col + 1}"
                    if self.table.bbox(item, column_id):
                        cells.append((item, column_id, (item, column_id) == self.cursor))
        while len(self.labels) < len(cells):
            self.labels.append(self._make_label())
        for index, label in enumerate(self.labels):
            if index >= len(cells):
                label.place_forget()
                continue
            item, column_id, is_cursor = cells[index]
            x, y, width, height = self.table.bbox(item, column_id)
            label.configure(
                text=self.table.set(item, column_id),
                bg="#CFE3FF" if is_cursor else "#E3EEFC",
                highlightthickness=2 if is_cursor else 1,
            )
            label.place(x=x, y=y, width=width, height=height)
            label.lift()

    def _make_label(self) -> tk.Label:
        label = tk.Label(
            self.table, anchor="w", bd=0, fg="#000000",
            highlightbackground="#2F80ED", highlightcolor="#2F80ED",
        )
        label.bind("<Button-1>", self._click)
        label.bind("<B1-Motion>", self._drag)
        label.bind("<Control-c>", self._copy)
        label.bind("<MouseWheel>", lambda event: self.table.yview_scroll(-1 if event.delta > 0 else 1, "units"))
        if self.on_context_menu:
            label.bind("<Button-3>", self.on_context_menu)
        return label

    def _move(self, d_row: int, d_col: int, extend: bool) -> None:
        if self.cursor is None:
            return
        item, column_id = self.cursor
        children = self.table.get_children()
        columns = len(self.table["columns"])
        row = max(0, min(len(children) - 1, self.table.index(item) + d_row))
        col = max(0, min(columns - 1, int(column_id[1:]) - 1 + d_col))
        self.set_cursor(children[row], f"#{col + 1}", extend=extend)
        self.table.see(children[row])
        self.table.update_idletasks()
        self.refresh()

    def _key(self, event: tk.Event) -> str | None:
        moves = {"Up": (-1, 0), "Down": (1, 0), "Left": (0, -1), "Right": (0, 1)}
        if event.keysym not in moves:
            return None
        self._move(*moves[event.keysym], extend=bool(event.state & 0x1))
        return "break"

    def _copy(self, _event: tk.Event | None = None) -> str:
        bounds = self.bounds()
        if bounds:
            row1, row2, col1, col2 = bounds
            lines = []
            for item in self.table.get_children()[row1 : row2 + 1]:
                lines.append("\t".join(self.table.set(item, f"#{col + 1}") for col in range(col1, col2 + 1)))
            self.table.clipboard_clear()
            self.table.clipboard_append("\n".join(lines))
        return "break"


class CampbellViewer:
    def __init__(self, root: tk.Tk, data: pd.DataFrame, source: Path, logger: str):
        self.root = root
        self.data = data
        self.source = source
        self.logger = logger
        self.location = logger_and_location_from_filename(source)[1]
        self.page = 0
        self.data2_page = 0
        self.chart_data = data
        self.removed_columns: set[str] = set()
        self.calc_columns: list[str] = []
        self.formula_errors: list[str] = []
        self.pivot_frames: dict[str, pd.DataFrame] = {}
        self.pivot_columns: dict[str, str] = {}
        self.pivot_page = 0
        self._data_changed = False
        self.figures: list[Figure] = []
        self.canvases: list[FigureCanvasTkAgg] = []
        self.chart_axes: list[tuple[FigureCanvasTkAgg, list[Any], bool]] = []
        self._tab_images: list[tk.PhotoImage] = []
        self._chart_origin_tab: str | None = None
        self._window_icon_handles: list[int] = []
        self._configure_window()
        self._build_shell()
        self._populate_tabs()
        self._apply_window_icon()
        self.root.after(100, self._activate_window)
        self._last_selected_tab = self.notebook.select()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

    def _activate_window(self) -> None:
        self.root.deiconify()
        self.root.state("zoomed")
        self.root.lift()
        self.root.focus_force()
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            hwnd = user32.GetAncestor(self.root.winfo_id(), 2) or self.root.winfo_id()
            user32.SetForegroundWindow(hwnd)

    def _apply_window_icon(self) -> None:
        icon = str(application_icon_path())
        self.root.iconbitmap(default=icon)
        self.root.iconbitmap(icon)
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        self.root.update_idletasks()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        set_class_long_ptr = getattr(user32, "SetClassLongPtrW", user32.SetClassLongW)
        set_class_long_ptr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_class_long_ptr.restype = ctypes.c_size_t

        hwnd = user32.GetAncestor(self.root.winfo_id(), 2) or self.root.winfo_id()
        for kind, metric, class_index in ((1, 11, -14), (0, 49, -34)):
            size = user32.GetSystemMetrics(metric)
            handle = user32.LoadImageW(None, icon, 1, size, size, 0x10)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self._window_icon_handles.append(handle)
            user32.SendMessageW(hwnd, 0x80, kind, handle)
            set_class_long_ptr(hwnd, class_index, handle)

    def _configure_window(self) -> None:
        self.root.title(f"Campbell Sci | {self.source.name}")
        self.root.iconbitmap(str(application_icon_path()))
        self.root.geometry("1380x860")
        self.root.state("zoomed")
        self.root.minsize(900, 600)
        self.root.configure(background="#F3F5F7")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#F3F5F7")
        style.configure("Toolbar.TFrame", background="#FFFFFF")
        style.configure("TLabel", background="#F3F5F7", foreground="#263442", font=("Segoe UI", 10))
        style.configure("Toolbar.TLabel", background="#FFFFFF", foreground="#263442", font=("Segoe UI", 10))
        style.configure("TButton", padding=(10, 6), font=("Segoe UI", 9))
        style.configure("TNotebook", background="#F3F5F7", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(0, 0), font=("Segoe UI", 9))
        style.layout("Scroll.TNotebook.Tab", [])
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 9), background="#FFFFFF", fieldbackground="#FFFFFF")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_shell(self) -> None:
        toolbar = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(14, 10))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text=t("toolbar.open_data"), command=self._choose_another_file).pack(side="left")
        ttk.Button(toolbar, text=t("toolbar.manage_columns"), command=self._manage_columns).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text=t("toolbar.modelling"), command=self._open_modelling).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text=t("toolbar.logdef_editor"), command=self._open_logdef_editor).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text=t("toolbar.formula_editor"), command=self._open_formula_editor).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text=t("pivot.editor"), command=self._open_pivot_editor).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text=t("toolbar.help"), command=self._open_help).pack(side="left", padx=(8, 0))
        self.auto_scale_y = tk.BooleanVar(value=get_auto_scale_y())
        ttk.Checkbutton(
            toolbar, text=t("toolbar.auto_scale_y"), variable=self.auto_scale_y,
            command=self._on_auto_scale_toggle,
        ).pack(side="left", padx=(14, 0))
        self.file_label = ttk.Label(toolbar, text=self.source.name, style="Toolbar.TLabel")
        self.file_label.pack(side="left", padx=(14, 0))
        languages = available_languages()
        self._language_codes = list(languages)
        language_box = ttk.Combobox(
            toolbar, values=list(languages.values()), state="readonly", width=14,
        )
        language_box.set(languages.get(current_language(), current_language()))
        language_box.bind("<<ComboboxSelected>>", lambda _event: self._change_language(language_box.current()))
        language_box.pack(side="right", padx=(10, 0))
        ttk.Label(toolbar, text=t("toolbar.language"), style="Toolbar.TLabel").pack(side="right", padx=(10, 4))
        self.status = tk.StringVar(value=t("status.rows_logger", rows=f"{len(self.data):,}", logger=self.logger))
        ttk.Label(toolbar, textvariable=self.status, style="Toolbar.TLabel").pack(side="right")
        tabbar = ttk.Frame(self.root)
        tabbar.pack(fill="x", padx=10)
        ttk.Button(tabbar, text="<", width=3, command=lambda: self.tab_canvas.xview_scroll(-2, "units")).pack(side="left")
        ttk.Button(tabbar, text=">", width=3, command=lambda: self.tab_canvas.xview_scroll(2, "units")).pack(side="right")
        self.tab_canvas = tk.Canvas(tabbar, height=38, highlightthickness=0, background="#F3F5F7")
        self.tab_canvas.pack(side="left", fill="x", expand=True)
        self.tab_strip = ttk.Frame(self.tab_canvas)
        self.tab_buttons: list[tk.Button] = []
        self.tab_canvas.create_window(0, 0, window=self.tab_strip, anchor="nw")
        self.tab_strip.bind("<Configure>", lambda _event: self.tab_canvas.configure(scrollregion=self.tab_canvas.bbox("all")))
        self.tab_canvas.bind("<MouseWheel>", lambda event: self.tab_canvas.xview_scroll(-1 if event.delta > 0 else 1, "units"))
        self.notebook = ttk.Notebook(self.root, style="Scroll.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        footer = ttk.Frame(self.root, padding=(10, 3))
        footer.pack(fill="x", side="bottom")
        ttk.Label(footer, text=t("footer.author")).pack(side="right")
        self._shell_widgets = [toolbar, tabbar, self.notebook, footer]

    def _change_language(self, index: int) -> None:
        code = self._language_codes[index]
        if code == current_language():
            return
        self._finish_cell_edit(save=True)
        apply_language(code)
        set_language(code)
        for widget in self._shell_widgets:
            widget.destroy()
        self._configure_window()
        self._build_shell()
        self._populate_tabs()
        self._last_selected_tab = self.notebook.select()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

    def _make_tab_image(self, label: str, color: str = "") -> tk.PhotoImage:
        font = tkfont.Font(root=self.root, family="Segoe UI", size=9)
        width = max(48, font.measure(label) + 24)
        height = font.metrics("linespace") + 14
        image = tk.PhotoImage(master=self.root, width=width, height=height)
        if color:
            try:
                red, green, blue = self.root.winfo_rgb(color)
            except tk.TclError as exc:
                raise ValueError(t("err.invalid_tab_color", color=color, label=label)) from exc
            rgb = (red, green, blue)
            background = "#{:02x}{:02x}{:02x}".format(
                *(round((channel / 65535) * 0.35 * 255 + 0.65 * 255) for channel in rgb)
            )
        else:
            background = "#E5E7EB"
        image.put(background, to=(0, 0, width, height))
        self._tab_images.append(image)
        return image

    def _populate_tabs(self) -> None:
        self._clear_tabs()
        self._add_data_tab()
        self._compute_calc()
        self._add_data2_tab()
        self._compute_pivots()
        self._add_pivot_tab()
        specs = load_chart_config(self.logger, LOGDEF_DIR, location=self.location)
        if not specs:
            specs = _fallback_charts(self.chart_data)
        specs = self._without_removed_columns(specs)
        if not specs:
            empty = ttk.Frame(self.notebook, padding=24)
            ttk.Label(empty, text=t("chart.no_numeric_columns")).pack(anchor="w")
            self.notebook.add(
                empty,
                text=t("tab.charts"),
                image=self._make_tab_image(t("tab.charts")),
                compound="center",
            )
            self._refresh_tab_strip()
            return
        headers = _header_lookup(self.chart_data)
        headers.update({name.upper(): name for name in self.pivot_columns})
        used_names: set[str] = set()
        failed_charts: list[str] = []
        for index, spec in enumerate(specs):
            name = spec["name"] or t("chart.default_name", number=index + 1)
            if name in used_names:
                name = f"{name[:26]} ({index + 1})"
            used_names.add(name)
            chart_spec = dict(spec)
            chart_spec["name"] = name
            page = ttk.Frame(self.notebook)
            self.notebook.add(
                page,
                text=name,
                image=self._make_tab_image(name, chart_spec["tab_color"]),
                compound="center",
            )
            try:
                self._draw_chart(page, chart_spec, headers, self.chart_data)
            except (KeyError, ValueError) as exc:
                self.notebook.forget(page)
                page.destroy()
                failed_charts.append(f"{name}: {exc}")
        self._refresh_tab_strip()
        if len(self.notebook.tabs()) > 3:
            self.notebook.select(self.notebook.tabs()[3])
        if failed_charts:
            message = t("chart.skipped", charts="\n".join(failed_charts))
            self.root.after(200, lambda: messagebox.showwarning(t("chart.skipped_title"), message, parent=self.root))

    def _without_removed_columns(self, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.removed_columns:
            return specs

        def is_removed(configured: str) -> bool:
            name = configured[:-10] if configured.lower().endswith("@secondary") else configured
            return name.strip().casefold() in self.removed_columns

        kept = []
        for spec in specs:
            columns = spec["columns"]
            if not columns:
                kept.append(spec)
                continue
            if spec.get("type") == "windroos":
                if not is_removed(columns[0]):
                    kept.append({**spec, "columns": [columns[0], *[c for c in columns[1:] if not is_removed(c)]]})
                continue
            remaining = [c for c in columns[1:] if not is_removed(c)]
            # Scatter charts need both columns; other charts survive while a series remains.
            if not remaining or (spec.get("type") == "scatter" and len(remaining) != len(columns) - 1):
                continue
            kept.append({**spec, "columns": [columns[0], *remaining]})
        return kept

    def _refresh_tab_strip(self) -> None:
        for child in self.tab_strip.winfo_children():
            child.destroy()
        self.tab_buttons.clear()
        for tab in self.notebook.tabs():
            options = self.notebook.tab(tab)
            button = tk.Button(
                self.tab_strip, text=options["text"], image=options["image"] or "",
                compound="center", relief="raised", borderwidth=1,
                command=lambda target=tab: self.notebook.select(target),
            )
            button.pack(side="left")
            self.tab_buttons.append(button)
            button.bind("<MouseWheel>", lambda event: self.tab_canvas.xview_scroll(-1 if event.delta > 0 else 1, "units"))
        self._highlight_tab()

    def _highlight_tab(self) -> None:
        selected = self.notebook.select()
        for tab, button in zip(self.notebook.tabs(), self.tab_buttons):
            button.configure(relief="sunken" if tab == selected else "raised")
            if tab == selected:
                self.root.after_idle(lambda target=button: self._show_selected_tab(target))

    def _show_selected_tab(self, button: tk.Widget) -> None:
        if not button.winfo_exists() or not self.tab_canvas.winfo_exists():
            return
        self.tab_strip.update_idletasks()
        width = self.tab_canvas.winfo_width()
        total = self.tab_strip.winfo_width()
        if width < 2 or total <= width:
            return
        left = self.tab_canvas.canvasx(0)
        start = button.winfo_x()
        end = start + button.winfo_width()
        if start < left:
            self.tab_canvas.xview_moveto(start / total)
        elif end > left + width:
            self.tab_canvas.xview_moveto((end - width) / total)

    def _compute_pivots(self) -> None:
        self.pivot_frames = {}
        self.pivot_bases: dict[str, pd.DataFrame] = {}
        self.pivot_columns = {}
        errors: list[str] = []
        try:
            definitions = load_pivots(pivot_path(self.logger, self.location, PIVOT_DIR))
            available = []
            for definition in definitions:
                if str(definition["column"]).strip().casefold() in getattr(self, "removed_columns", ()):
                    continue
                if definition["column"] not in self.chart_data.columns:
                    errors.append(t("err.column_not_in_data", name=definition["column"]))
                else:
                    available.append(definition)
            definitions = available
            self.pivot_frames = calculate_pivots(self.chart_data, self.header_row_count, definitions)
            self.pivot_bases = {period: frame.copy() for period, frame in self.pivot_frames.items()}
            for period, frame in self.pivot_frames.items():
                formulas = load_formulas(self.logger, self.location, PIVOT_FORMULA_DIR / period)
                macros = load_funcdefs(FUNCTION_DIR)
                calculated, _names, formula_errors = apply_formulas(frame, formulas, 0, macros)
                errors.extend(f"{t('pivot.period.' + period)}: {error}" for error in formula_errors)
                self.pivot_frames[period] = calculated
            existing = {str(c).casefold() for c in self.chart_data.columns}
            for period, frame in self.pivot_frames.items():
                for column in frame.columns[1:]:
                    if str(column).casefold() in existing:
                        raise ValueError(t("pivot.duplicate", name=column))
                    existing.add(str(column).casefold())
                    self.pivot_columns[str(column)] = period
        except (OSError, ValueError) as exc:
            self.pivot_frames = {}
            self.pivot_bases = {}
            self.pivot_columns = {}
            errors.append(str(exc))
        if errors:
            self.root.after(200, lambda: messagebox.showwarning(t("pivot.editor"), "\n".join(errors), parent=self.root))

    def _add_pivot_tab(self) -> None:
        page = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(page, text=t("tab.pivot"), image=self._make_tab_image(t("tab.pivot")), compound="center")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        controls = ttk.Frame(page)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(controls, text=t("pivot.interval")).pack(side="left")
        self.pivot_periods = [period for period in PERIODS if period in self.pivot_frames]
        self.pivot_choice = ttk.Combobox(controls, values=[t("pivot.period." + p) for p in self.pivot_periods], state="readonly", width=15)
        self.pivot_choice.pack(side="left", padx=8)
        self.pivot_choice.bind("<<ComboboxSelected>>", self._change_pivot_period)
        ttk.Button(controls, text=t("pivot.formulas"), command=self._open_pivot_formulas).pack(side="left", padx=8)
        self.pivot_table = ttk.Treeview(page, show="headings", selectmode="none")
        ybar = ttk.Scrollbar(page, orient="vertical", command=self.pivot_table.yview)
        xbar = ttk.Scrollbar(page, orient="horizontal", command=self.pivot_table.xview)
        self.pivot_table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.pivot_table.grid(row=1, column=0, sticky="nsew")
        ybar.grid(row=1, column=1, sticky="ns")
        xbar.grid(row=2, column=0, sticky="ew")
        self.pivot_selection = ReadOnlyCellSelection(self.pivot_table)
        bottom = ttk.Frame(page)
        bottom.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.pivot_page_label = tk.StringVar()
        ttk.Button(bottom, text=t("button.previous"), command=lambda: self._change_pivot_page(-1)).pack(side="left")
        ttk.Label(bottom, textvariable=self.pivot_page_label).pack(side="left", padx=12)
        ttk.Button(bottom, text=t("button.next"), command=lambda: self._change_pivot_page(1)).pack(side="left")
        ColumnSearch(bottom, self.pivot_table,
                     lambda index: self._select_search_column(self.pivot_table, self.pivot_selection, index)).pack(side="right")
        if self.pivot_periods:
            self.pivot_choice.current(0)
        self._change_pivot_period()

    def _change_pivot_period(self, _event: object = None) -> None:
        self.pivot_page = 0
        self._show_pivot_page()

    def _show_pivot_page(self) -> None:
        table = self.pivot_table
        self.pivot_selection.set_cursor(None)
        table.delete(*table.get_children())
        index = self.pivot_choice.current()
        if index < 0:
            table.configure(columns=())
            self.pivot_page_label.set(t("pivot.empty"))
            return
        frame = self.pivot_frames[self.pivot_periods[index]]
        columns = list(frame.columns)
        table.configure(columns=columns)
        for column in columns:
            table.heading(column, text=column)
            table.column(column, width=max(120, min(220, len(str(column)) * 9)), minwidth=80, stretch=False)
        start = self.pivot_page * PAGE_SIZE
        end = min(len(frame), start + PAGE_SIZE)
        for row in frame.iloc[start:end].itertuples(index=False, name=None):
            table.insert("", "end", values=["" if pd.isna(value) else f"{value:.6g}" if isinstance(value, float) else str(value) for value in row])
        self.pivot_page_label.set(t("page.label", first=f"{start + 1 if end else 0:,}", last=f"{end:,}", count=f"{len(frame):,}", page=self.pivot_page + 1, pages=max(1, math.ceil(len(frame) / PAGE_SIZE))))

    def _change_pivot_page(self, direction: int) -> None:
        index = self.pivot_choice.current()
        if index < 0:
            return
        count = len(self.pivot_frames[self.pivot_periods[index]])
        self.pivot_page = max(0, min(max(0, math.ceil(count / PAGE_SIZE) - 1), self.pivot_page + direction))
        self._show_pivot_page()

    def _compute_calc(self) -> None:
        errors: list[str] = []
        try:
            formulas = load_formulas(self.logger, self.location, FORMULA_DIR)
        except ValueError as exc:
            formulas = []
            errors.append(str(exc))
        try:
            macros = load_funcdefs(FUNCTION_DIR)
        except ValueError as exc:
            macros = {}
            errors.append(str(exc))
        self.chart_data, self.calc_columns, formula_errors = apply_formulas(
            self.data, formulas, self.header_row_count, macros
        )
        self.formula_errors = errors + formula_errors

    def _add_data2_tab(self) -> None:
        page = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(
            page,
            text=t("tab.calc"),
            image=self._make_tab_image(t("tab.calc")),
            compound="center",
        )
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)
        self.data2_info = tk.StringVar()
        ttk.Label(page, textvariable=self.data2_info, wraplength=1100).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        columns = [str(self.chart_data.columns[0])] + list(self.calc_columns)
        table = ttk.Treeview(page, columns=columns, show="headings", selectmode="none")
        ybar = ttk.Scrollbar(page, orient="vertical", command=table.yview)
        xbar = ttk.Scrollbar(page, orient="horizontal", command=table.xview)
        table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        table.grid(row=1, column=0, sticky="nsew")
        ybar.grid(row=1, column=1, sticky="ns")
        xbar.grid(row=2, column=0, sticky="ew")
        for column in columns:
            table.heading(column, text=column)
            table.column(column, width=max(120, min(220, len(column) * 9)), minwidth=80, stretch=False)
        controls = ttk.Frame(page)
        controls.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.data2_page_label = tk.StringVar()
        ttk.Button(controls, text=t("button.previous"), command=lambda: self._change_data2_page(-1)).pack(side="left")
        ttk.Label(controls, textvariable=self.data2_page_label).pack(side="left", padx=12)
        ttk.Button(controls, text=t("button.next"), command=lambda: self._change_data2_page(1)).pack(side="left")
        self.data2_table = table
        self.data2_selection = ReadOnlyCellSelection(table)
        ColumnSearch(controls, table,
                     lambda index: self._select_search_column(table, self.data2_selection, index)).pack(side="right")
        self.data2_columns = columns
        self._show_data2_page()
        if self.formula_errors:
            self.root.after(200, lambda: messagebox.showwarning(
                t("dialog.formulas"), t("calc.formula_errors") + "\n" + "\n".join(self.formula_errors),
                parent=self.root))

    def _show_data2_page(self) -> None:
        table = self.data2_table
        self.data2_selection.set_cursor(None)
        table.delete(*table.get_children())
        start = self.header_row_count + self.data2_page * PAGE_SIZE
        end = min(len(self.chart_data), start + PAGE_SIZE)
        frame = self.chart_data.iloc[start:end][self.data2_columns]
        for row in frame.itertuples(index=False, name=None):
            values = []
            for value in row:
                if isinstance(value, float):
                    value = "" if pd.isna(value) else f"{value:.6g}"
                values.append(value)
            table.insert("", "end", values=values)
        count = len(self.chart_data) - self.header_row_count
        total_pages = max(1, math.ceil(count / PAGE_SIZE))
        self.data2_page_label.set(t(
            "page.label", first=f"{start - self.header_row_count + 1:,}", last=f"{end - self.header_row_count:,}",
            count=f"{count:,}", page=self.data2_page + 1, pages=total_pages,
        ))
        errors = " | " + t("calc.errors", count=len(self.formula_errors), errors="; ".join(self.formula_errors)) if self.formula_errors else ""
        self.data2_info.set(t("calc.info", count=len(self.calc_columns), errors=errors))

    def _change_data2_page(self, direction: int) -> None:
        count = len(self.chart_data) - self.header_row_count
        total_pages = max(1, math.ceil(count / PAGE_SIZE))
        self.data2_page = max(0, min(total_pages - 1, self.data2_page + direction))
        self._show_data2_page()

    def _clear_tabs(self) -> None:
        for tab in self.notebook.tabs():
            self.notebook.forget(tab)
            self.root.nametowidget(tab).destroy()
        self._tab_images.clear()
        for figure in self.figures:
            plt.close(figure)
        self.figures.clear()
        self.canvases.clear()
        self.chart_axes.clear()

    def _on_notebook_tab_changed(self, _event: tk.Event) -> None:
        self._highlight_tab()
        selected_tab = self.notebook.select()
        if not selected_tab:
            return
        previous_tab = self._last_selected_tab
        self._last_selected_tab = selected_tab
        tabs = self.notebook.tabs()
        if len(tabs) < 2 or previous_tab != tabs[0] or selected_tab == tabs[0]:
            return

        self._finish_cell_edit(save=True)
        if not self._data_changed:
            return
        self._data_changed = False
        selected_index = tabs.index(selected_tab)
        self._populate_tabs()
        tabs = self.notebook.tabs()
        self.notebook.select(tabs[min(selected_index, len(tabs) - 1)])
        self._last_selected_tab = self.notebook.select()

    def _add_data_tab(self) -> None:
        page = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(
            page,
            text=t("tab.data"),
            image=self._make_tab_image(t("tab.data")),
            compound="center",
        )
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)
        columns = [str(column) for column in self.data.columns]
        self.header_row_count = min(
            int(self.data.attrs.get("header_row_count", HEADER_ROWS)),
            len(self.data),
        )
        frozen_table = ttk.Treeview(
            page, columns=columns, show="headings", height=self.header_row_count, selectmode="none"
        )
        table = ttk.Treeview(page, columns=columns, show="", selectmode="none")
        ybar = ttk.Scrollbar(page, orient="vertical", command=lambda *args: self._scroll_table(table, "y", *args))
        xbar = ttk.Scrollbar(page, orient="horizontal", command=lambda *args: self._scroll_table(table, "x", *args))
        table.configure(
            yscrollcommand=lambda first, last: (ybar.set(first, last), self._place_cursor()),
            xscrollcommand=lambda first, last: (self._on_table_xscroll(xbar, frozen_table, first, last), self._place_cursor()),
        )
        frozen_table.grid(row=0, column=0, sticky="ew")
        table.grid(row=1, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, rowspan=2, sticky="ns")
        xbar.grid(row=2, column=0, sticky="ew")
        for column in columns:
            width = max(105, min(220, len(column) * 9))
            frozen_table.heading(column, text=column)
            frozen_table.column(column, width=width, minwidth=80, stretch=False)
            table.column(column, width=width, minwidth=80, stretch=False)
        for row in self.data.iloc[:self.header_row_count].itertuples(index=False, name=None):
            values = ["" if pd.isna(value) else str(value) for value in row]
            frozen_table.insert("", "end", values=values)
        frozen_table.bind("<B1-Motion>", lambda _event: self._sync_frozen_widths(frozen_table, table))
        frozen_table.bind("<ButtonRelease-1>", lambda _event: self._sync_frozen_widths(frozen_table, table))
        controls = ttk.Frame(page)
        controls.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.page_label = tk.StringVar()
        ttk.Button(controls, text=t("button.previous"), command=lambda: self._change_page(-1)).pack(side="left")
        ttk.Label(controls, textvariable=self.page_label).pack(side="left", padx=12)
        ttk.Button(controls, text=t("button.next"), command=lambda: self._change_page(1)).pack(side="left")
        ttk.Button(controls, text=t("button.save"), command=self._save_data).pack(side="right")
        self.table = table
        self.frozen_table = frozen_table
        self._marked_column_id: str | None = None
        self._marked_column_index: int | None = None
        self._cursor: tuple[str, str] | None = None
        self._anchor: tuple[str, str] | None = None
        self._cell_labels: list[tk.Label] = []
        table.bind("<Button-1>", self._on_table_click)
        table.bind("<B1-Motion>", self._on_table_drag)
        table.bind("<Double-1>", self._begin_cell_edit)
        table.bind("<Key>", self._on_table_key)
        self._edit_entry: tk.Entry | None = None
        ColumnSearch(controls, table, self._select_data_search_column).pack(side="right")
        self._add_spreadsheet_menu(table)
        self._show_page()

    @staticmethod
    def _visible_table_item(table: ttk.Treeview) -> str:
        return next((item for item in table.get_children() if table.bbox(item)), "")

    def _select_data_search_column(self, index: int) -> None:
        self._finish_cell_edit(save=True)
        item = self._cursor[0] if self._cursor else self._visible_table_item(self.table)
        if item:
            self._set_cursor(item, f"#{index + 1}")
        self._mark_column(index)
        self.table.focus_set()

    def _select_search_column(self, table: ttk.Treeview, selection: ReadOnlyCellSelection, index: int) -> None:
        item = selection.cursor[0] if selection.cursor else self._visible_table_item(table)
        if item:
            selection.set_cursor(item, f"#{index + 1}")
        table.focus_set()

    def _make_cell_label(self, cursor: bool) -> tk.Label:
        label = tk.Label(
            self.table, anchor="w", bd=0, fg="#000000",
            bg="#CFE3FF" if cursor else "#E3EEFC",
            highlightthickness=2 if cursor else 1,
            highlightbackground="#2F80ED", highlightcolor="#2F80ED",
        )
        label.bind("<Button-1>", self._on_table_click)
        label.bind("<B1-Motion>", self._on_table_drag)
        label.bind("<Double-1>", self._begin_cell_edit)
        label.bind("<Button-3>", lambda event: self._show_spreadsheet_menu(event, self._spreadsheet_menu))
        label.bind("<MouseWheel>", lambda event: self.table.yview_scroll(-1 if event.delta > 0 else 1, "units"))
        return label

    def _cell_at(self, event: tk.Event) -> tuple[str, str] | None:
        x = event.x_root - self.table.winfo_rootx()
        y = event.y_root - self.table.winfo_rooty()
        item = self.table.identify_row(y)
        column_id = self.table.identify_column(x)
        return (item, column_id) if item and column_id else None

    def _on_table_xscroll(self, xbar: ttk.Scrollbar, frozen_table: ttk.Treeview, first: str, last: str) -> None:
        xbar.set(first, last)
        frozen_table.xview_moveto(float(first))

    def _sync_frozen_widths(self, frozen_table: ttk.Treeview, table: ttk.Treeview) -> None:
        for column in frozen_table["columns"]:
            table.column(column, width=frozen_table.column(column, "width"))


    def _row_position(self, item: str) -> int:
        return self.header_row_count + self.page * PAGE_SIZE + self.table.index(item)


    def _begin_cell_edit(self, event: tk.Event) -> str:
        cell = self._cell_at(event)
        if cell:
            self._set_cursor(*cell)
            self._start_cell_edit(*cell)
        return "break"

    def _on_table_click(self, event: tk.Event) -> str:
        self.table.focus_set()
        self._finish_cell_edit(save=True)
        cell = self._cell_at(event)
        if cell:
            self._set_cursor(*cell, extend=bool(event.state & 0x1))
        return "break"

    def _on_table_drag(self, event: tk.Event) -> str:
        cell = self._cell_at(event)
        if cell and cell != self._cursor:
            self._set_cursor(*cell, extend=True)
        return "break"

    def _set_cursor(self, item: str | None, column_id: str | None = None, extend: bool = False) -> None:
        self._cursor = (item, column_id) if item and column_id else None
        if not extend or self._anchor is None or self._cursor is None:
            self._anchor = self._cursor
        self._place_cursor()

    def _selection_bounds(self) -> tuple[int, int, int, int] | None:
        if self._cursor is None or self._anchor is None:
            return None
        rows = sorted((self.table.index(self._anchor[0]), self.table.index(self._cursor[0])))
        cols = sorted((int(self._anchor[1][1:]) - 1, int(self._cursor[1][1:]) - 1))
        return rows[0], rows[1], cols[0], cols[1]

    def _place_cursor(self) -> None:
        bounds = self._selection_bounds()
        cells: list[tuple[str, str, bool]] = []
        if bounds:
            row1, row2, col1, col2 = bounds
            children = self.table.get_children()
            first = self.table.identify_row(1)
            start = max(row1, self.table.index(first)) if first else row1
            for row in range(start, row2 + 1):
                item = children[row]
                if not self.table.bbox(item, "#1"):
                    break
                for col in range(col1, col2 + 1):
                    column_id = f"#{col + 1}"
                    if self.table.bbox(item, column_id):
                        cells.append((item, column_id, (item, column_id) == self._cursor))
        while len(self._cell_labels) < len(cells):
            self._cell_labels.append(self._make_cell_label(cursor=False))
        for index, label in enumerate(self._cell_labels):
            if index >= len(cells):
                label.place_forget()
                continue
            item, column_id, is_cursor = cells[index]
            x, y, width, height = self.table.bbox(item, column_id)
            label.configure(
                text=self.table.set(item, column_id),
                bg="#CFE3FF" if is_cursor else "#E3EEFC",
                highlightthickness=2 if is_cursor else 1,
            )
            label.place(x=x, y=y, width=width, height=height)
            label.lift()

    def _move_cursor(self, d_row: int, d_col: int, extend: bool = False) -> None:
        if self._cursor is None:
            return
        item, column_id = self._cursor
        children = self.table.get_children()
        row = max(0, min(len(children) - 1, self.table.index(item) + d_row))
        col = max(0, min(len(self.data.columns) - 1, int(column_id[1:]) - 1 + d_col))
        item = children[row]
        self._set_cursor(item, f"#{col + 1}", extend=extend)
        self.table.see(item)
        if not self.table.bbox(item, f"#{col + 1}"):
            self._center_on_cell(item, col)
            self._place_cursor()

    def _on_table_key(self, event: tk.Event) -> str | None:
        if self._edit_entry is not None:
            return None
        moves = {"Up": (-1, 0), "Down": (1, 0), "Left": (0, -1), "Right": (0, 1)}
        if event.keysym in moves:
            self._move_cursor(*moves[event.keysym], extend=bool(event.state & 0x1))
        elif event.keysym in ("Return", "KP_Enter", "F2"):
            self._edit_cursor_cell()
        elif event.keysym == "Delete":
            self._clear_cursor_cell()
        elif event.char and event.char.isprintable() and not event.state & 0x4:
            self._edit_cursor_cell(event.char)
        else:
            return None
        return "break"

    def _edit_cursor_cell(self, initial: str | None = None) -> None:
        if self._cursor is not None:
            self._set_cursor(*self._cursor)
            self._start_cell_edit(*self._cursor, initial)

    def _clear_cursor_cell(self) -> None:
        bounds = self._selection_bounds()
        if bounds is None:
            return
        row1, row2, col1, col2 = bounds
        children = self.table.get_children()
        first = self.header_row_count + self.page * PAGE_SIZE
        self.data.iloc[first + row1 : first + row2 + 1, col1 : col2 + 1] = float("nan")
        for item in children[row1 : row2 + 1]:
            for col in range(col1, col2 + 1):
                self.table.set(item, f"#{col + 1}", "")
        self._data_changed = True
        self._place_cursor()
        self.status.set(t("status.cells_cleared", count=(row2 - row1 + 1) * (col2 - col1 + 1)))

    def _start_cell_edit(self, item: str, column_id: str, initial: str | None = None) -> None:
        table = self.table
        self._finish_cell_edit(save=True)
        box = table.bbox(item, column_id)
        if not box:
            return
        x, y, width, height = box
        column_index = int(column_id.replace("#", "")) - 1
        entry = tk.Entry(table, highlightthickness=2, highlightbackground="#2F80ED", highlightcolor="#2F80ED")
        if initial is None:
            entry.insert(0, table.set(item, column_id))
            entry.select_range(0, "end")
        else:
            entry.insert(0, initial)
        entry.place(x=x, y=y, width=width, height=height)
        entry.focus_set()
        entry.bind("<Return>", lambda _event: self._commit_and_move_down())
        entry.bind("<KP_Enter>", lambda _event: self._commit_and_move_down())
        entry.bind("<Escape>", lambda _event: self._cancel_edit())
        entry.bind("<FocusOut>", lambda _event: self._finish_cell_edit(save=True))
        self._edit_entry = entry
        self._edit_target = (item, column_id, column_index)

    def _commit_and_move_down(self) -> None:
        self._finish_cell_edit(save=True)
        self.table.focus_set()
        self._move_cursor(1, 0)

    def _cancel_edit(self) -> None:
        self._finish_cell_edit(save=False)
        self.table.focus_set()

    def _finish_cell_edit(self, save: bool) -> None:
        entry = self._edit_entry
        if entry is None:
            return
        self._edit_entry = None
        item, column_id, _column_index = self._edit_target
        new_text = entry.get()
        entry.destroy()
        if save:
            self._store_cell_value(item, column_id, new_text)

    def _store_cell_value(self, item: str, column_id: str, new_text: str) -> None:
        column_index = int(column_id.replace("#", "")) - 1
        row_position = self._row_position(item)
        column = self.data.columns[column_index]
        stripped = new_text.strip()
        value: Any = float("nan") if stripped in ("", "nan", "NaN", "NAN") else stripped
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                pass
        self.data.iloc[row_position, column_index] = value
        self._data_changed = True
        display = "" if pd.isna(value) else str(value)
        self.table.set(item, column_id, display)
        self._place_cursor()
        self.status.set(t("status.cell_changed", row=row_position + 1, column=column))

    def _save_data(self) -> None:
        original_extension = self.source.suffix or ".dat"
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title=t("dialog.save_data"),
            initialdir=str(self.source.parent),
            initialfile=f"{self.source.stem}{t('file.edited_suffix')}{original_extension}",
            defaultextension=original_extension,
            filetypes=[(f"Data (*{original_extension})", f"*{original_extension}"), (t("filetype.all"), "*.*")],
        )
        if not target:
            return
        try:
            delimiter = self.data.attrs.get("generic_csv")
            with open(target, "w", encoding="utf-8", newline="") as handle:
                if delimiter:
                    self.data.to_csv(handle, sep=delimiter, index=False, lineterminator="\n")
                    self.status.set(t("status.saved", name=Path(target).name))
                    return
                for line in _read_preamble_lines(self.source):
                    handle.write(line + "\n")
                writer = csv.writer(handle, quoting=csv.QUOTE_NONNUMERIC)
                writer.writerow(list(self.data.columns))
                header_rows = self.data.iloc[: self.header_row_count]
                data_rows = self.data.iloc[self.header_row_count :]
                for row in header_rows.itertuples(index=False, name=None):
                    writer.writerow([_format_toa5_cell(value, "") for value in row])
                for row in data_rows.itertuples(index=False, name=None):
                    writer.writerow([_format_toa5_cell(value, "NAN") for value in row])
            self.status.set(t("status.saved", name=Path(target).name))
        except Exception as exc:
            messagebox.showerror(t("dialog.save_failed"), str(exc), parent=self.root)



    def _scroll_table(self, table: ttk.Treeview, axis: str, *args: Any) -> None:
        if axis == "x":
            table.xview(*args)
        else:
            table.yview(*args)

    def _add_spreadsheet_menu(self, table: ttk.Treeview) -> None:
        menu = tk.Menu(table, tearoff=False)
        for item in load_menu_config("spreadsheet"):
            menu.add_command(
                label=item["label"],
                command=lambda action=item["action"]: self._run_spreadsheet_action(action),
            )
        self._spreadsheet_menu = menu
        table.bind("<Button-3>", lambda event: self._show_spreadsheet_menu(event, menu))

    def _show_spreadsheet_menu(self, event: tk.Event, menu: tk.Menu) -> str:
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _run_spreadsheet_action(self, action: str) -> None:
        if action == "clear_range":
            self._clear_marked_column_selection()
            return
        if action != "return_to_chart":
            return
        if self._chart_origin_tab in self.notebook.tabs():
            self.notebook.select(self._chart_origin_tab)
        elif len(self.notebook.tabs()) > 1:
            self.notebook.select(self.notebook.tabs()[1])

    def _clear_marked_column_selection(self) -> None:
        if self._cursor is None:
            messagebox.showinfo("Campbell Sci", t("msg.click_cell_first"), parent=self.root)
            return
        self._clear_cursor_cell()


    def _show_page(self) -> None:
        self._set_cursor(None)
        self.table.delete(*self.table.get_children())
        measurement_count = len(self.data) - self.header_row_count
        start = self.header_row_count + self.page * PAGE_SIZE
        end = min(len(self.data), start + PAGE_SIZE)
        for row in self.data.iloc[start:end].itertuples(index=False, name=None):
            values = ["" if pd.isna(value) else str(value) for value in row]
            self.table.insert("", "end", values=values)
        total_pages = max(1, math.ceil(measurement_count / PAGE_SIZE))
        self.page_label.set(t(
            "page.label", first=f"{start - self.header_row_count + 1:,}", last=f"{end - self.header_row_count:,}",
            count=f"{measurement_count:,}", page=self.page + 1, pages=total_pages,
        ))

    def _change_page(self, direction: int) -> None:
        measurement_count = len(self.data) - self.header_row_count
        total_pages = max(1, math.ceil(measurement_count / PAGE_SIZE))
        self.page = max(0, min(total_pages - 1, self.page + direction))
        self._show_page()

    def _draw_chart(
        self,
        page: ttk.Frame,
        spec: dict[str, Any],
        headers: dict[str, str],
        data: pd.DataFrame,
    ) -> None:
        figure = Figure(figsize=(10, 6), dpi=100, facecolor="#FFFFFF")
        axes = figure.add_subplot(111)
        axes.set_facecolor("#FFFFFF")
        axes.grid(True, color="#DCE2E8", linewidth=0.7, alpha=0.8)
        axes.spines["top"].set_visible(False)
        axes.spines["right"].set_visible(False)
        axes.tick_params(colors="#344454", labelsize=9)
        axes.set_title(spec["name"], loc="left", fontsize=13, fontweight="bold", color="#1F2D3A", pad=14)
        chart_type = spec["type"]

        if chart_type == "windroos":
            axes = self._draw_wind_rose(figure, axes, spec, headers, data)
            axes_list = [axes]
            is_wind_rose = True
        elif chart_type == "scatter":
            self._draw_scatter(axes, spec, headers, data)
            axes_list = [axes]
            is_wind_rose = False
        elif chart_type in {"bar_grouped", "bar_range", "bar_grouped_range"}:
            self._draw_special_bars(axes, spec, headers, data)
            axes_list = [axes]
            is_wind_rose = False
        else:
            axes_list = self._draw_lines(figure, axes, spec, headers, data)
            is_wind_rose = False

        canvas = FigureCanvasTkAgg(figure, master=page)
        canvas_widget = canvas.get_tk_widget()
        canvas_widget.pack(fill="both", expand=True, padx=10, pady=10)
        canvas.draw()
        canvas.mpl_connect("scroll_event", lambda event, target=canvas, chart_axes=axes_list, polar=is_wind_rose: self._on_scroll(event, target, chart_axes, polar))
        canvas_widget.bind("<Motion>", lambda event, target=canvas, chart_axes=axes_list: self._on_chart_hover(event, target, chart_axes))
        canvas_widget.bind("<Leave>", lambda _event: self._hide_hover_tip())
        canvas_widget.bind("<Button-3>", lambda event, target=canvas, chart_axes=axes_list: self._show_chart_menu(event, target, chart_axes))
        self._add_chart_menu(page, canvas, axes_list, spec, headers, is_wind_rose)
        self.figures.append(figure)
        self.canvases.append(canvas)
        self.chart_axes.append((canvas, axes_list, is_wind_rose))

    def _add_chart_menu(
        self,
        page: ttk.Frame,
        canvas: FigureCanvasTkAgg,
        axes_list: list[Any],
        spec: dict[str, Any],
        headers: dict[str, str],
        is_wind_rose: bool,
    ) -> None:
        menu = tk.Menu(page, tearoff=False)
        for item in load_menu_config():
            menu.add_command(
                label=item["label"],
                command=lambda action=item["action"], target=canvas, chart_axes=axes_list: self._run_chart_action(action, target, chart_axes),
            )
        canvas._campbell_context_menu = menu
        canvas._campbell_chart_spec = spec
        canvas._campbell_chart_headers = headers
        canvas._campbell_is_wind_rose = is_wind_rose
        canvas._campbell_data_target = None

    def _on_chart_hover(self, event: tk.Event, canvas: FigureCanvasTkAgg, axes_list: list[Any]) -> None:
        target = self._find_data_target(event, canvas, axes_list)
        if target is None:
            self._hide_hover_tip()
            return
        row_index, column = target
        try:
            if column in self.pivot_columns:
                frame = self.pivot_frames[self.pivot_columns[column]]
                value = frame.loc[row_index, column]
                stamp = frame.loc[row_index, frame.columns[0]]
            else:
                position = self.chart_data.index.get_loc(row_index)
                value = self.chart_data.iloc[position][column]
                stamp = self.chart_data.iloc[position, 0]
        except (KeyError, TypeError, IndexError):
            self._hide_hover_tip()
            return
        value_text = "" if pd.isna(value) else (f"{value:.6g}" if isinstance(value, float) else str(value))
        text = f"{column}\n{stamp}\n{t('hover.value')}: {value_text}"
        tip = getattr(self, "_hover_tip", None)
        if tip is None:
            tip = tk.Toplevel(self.root)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            label = tk.Label(tip, justify="left", background="#FFFFE1", relief="solid", borderwidth=1, padx=6, pady=3)
            label.pack()
            tip._label = label
            self._hover_tip = tip
        tip._label.configure(text=text)
        tip.wm_geometry(f"+{event.x_root + 16}+{event.y_root + 16}")

    def _hide_hover_tip(self) -> None:
        tip = getattr(self, "_hover_tip", None)
        if tip is not None:
            tip.destroy()
            self._hover_tip = None

    def _show_chart_menu(self, event: tk.Event, canvas: FigureCanvasTkAgg, axes_list: list[Any]) -> str:
        menu = getattr(canvas, "_campbell_context_menu", None)
        if menu is not None:
            canvas._campbell_data_target = self._find_data_target(event, canvas, axes_list)
            menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _find_data_target(self, event: tk.Event, canvas: FigureCanvasTkAgg, axes_list: list[Any]) -> tuple[Any, str] | None:
        height = canvas.get_tk_widget().winfo_height()
        click = (event.x, height - event.y)
        nearest = None
        nearest_distance = float("inf")
        bar_target = None
        for axes in axes_list:
            for patch in axes.patches:
                target = getattr(patch, "_campbell_target", None)
                if target is not None and patch.contains_point(click):
                    bar_target = target
            for line in axes.get_lines():
                points = line.get_xydata()
                if not len(points) or not hasattr(line, "_campbell_row_indices"):
                    continue
                screen_points = axes.transData.transform(points)
                distances = ((screen_points[:, 0] - click[0]) ** 2 + (screen_points[:, 1] - click[1]) ** 2)
                point_index = int(distances.argmin())
                distance = float(distances[point_index])
                if distance < nearest_distance:
                    rows = line._campbell_row_indices
                    nearest = (rows[point_index], line._campbell_column)
                    nearest_distance = distance
            for points in axes.collections:
                if not hasattr(points, "_campbell_row_indices"):
                    continue
                offsets = points.get_offsets()
                if not len(offsets):
                    continue
                screen_points = axes.transData.transform(offsets)
                distances = ((screen_points[:, 0] - click[0]) ** 2 + (screen_points[:, 1] - click[1]) ** 2)
                point_index = int(distances.argmin())
                distance = float(distances[point_index])
                if distance < nearest_distance:
                    rows = points._campbell_row_indices
                    nearest = (rows[point_index], points._campbell_column)
                    nearest_distance = distance
        return nearest if nearest_distance <= 225 else bar_target

    def _run_chart_action(self, action: str, canvas: FigureCanvasTkAgg, axes_list: list[Any]) -> None:
        if action == "go_to_data":
            self._go_to_data(canvas, canvas._campbell_data_target)
        elif action == "split":
            self._split_chart(canvas, axes_list)
        elif action == "same_axis":
            self._use_same_axis(canvas, axes_list)
        elif action == "rescale":
            self._restore_chart(canvas, axes_list)
        canvas.draw_idle()

    def _go_to_data(self, canvas: FigureCanvasTkAgg, target: tuple[Any, str] | None) -> None:
        if target is None:
            return
        self._chart_origin_tab = str(canvas.get_tk_widget().master)
        row_index, column = target
        if column in self.pivot_columns:
            period = self.pivot_columns[column]
            frame = self.pivot_frames[period]
            position = int(frame.index.get_indexer([row_index])[0])
            self.pivot_choice.current(self.pivot_periods.index(period))
            self.pivot_page = position // PAGE_SIZE
            self._show_pivot_page()
            self.notebook.select(self.notebook.tabs()[2])
            item = self.pivot_table.get_children()[position % PAGE_SIZE]
            self.pivot_selection.set_cursor(item, f"#{list(frame.columns).index(column) + 1}")
            self.pivot_table.see(item)
            self.pivot_table.focus_set()
            return
        try:
            row_position = self.data.index.get_loc(row_index)
        except KeyError:
            return
        measurement_position = int(row_position) - self.header_row_count
        if measurement_position < 0:
            return
        if column not in self.data.columns:
            self.data2_page = measurement_position // PAGE_SIZE
            self._show_data2_page()
            self.notebook.select(self.notebook.tabs()[1])
            item = self.data2_table.get_children()[measurement_position % PAGE_SIZE]
            self.data2_selection.set_cursor(item, f"#{self.data2_columns.index(column) + 1}") if column in self.data2_columns else self.data2_selection.set_cursor(item, "#1")
            self.data2_table.see(item)
            self.data2_table.focus_set()
            return
        self.page = measurement_position // PAGE_SIZE
        self._show_page()
        self.notebook.select(self.notebook.tabs()[0])
        item = self.table.get_children()[measurement_position % PAGE_SIZE]
        column_index = list(self.data.columns).index(column)
        self._center_on_cell(item, column_index)
        self._set_cursor(item, f"#{column_index + 1}")
        self.table.focus_set()
        self._mark_column(column_index)

    def _center_on_cell(self, item: str, column_index: int) -> None:
        table = self.table
        table.update_idletasks()
        children = table.get_children()
        if not children:
            return
        total = len(children)
        index = table.index(item)
        visible_rows = max(1, table.winfo_height() // 24)
        target_index = max(0, min(total - 1, index - visible_rows // 2))
        table.yview_moveto(target_index / max(1, total - 1))
        columns = list(self.data.columns)
        widths = [table.column(str(column), "width") for column in columns]
        total_width = sum(widths) or 1
        before_width = sum(widths[:column_index])
        visible_width = table.winfo_width() or total_width
        target_left = before_width + widths[column_index] / 2 - visible_width / 2
        fraction = max(0.0, min(1.0, target_left / total_width))
        table.xview_moveto(fraction)
        table.update_idletasks()

    def _mark_column(self, column_index: int) -> None:
        if self._marked_column_id is not None:
            previous_index = self._marked_column_index
            self.frozen_table.heading(self._marked_column_id, text=str(self.data.columns[previous_index]), image="")
        column = self.data.columns[column_index]
        column_id = f"#{column_index + 1}"
        marker = tk.PhotoImage(width=12, height=12)
        marker.put("#2F80ED", to=(0, 0, 12, 12))
        self._marked_column_marker = marker
        self.frozen_table.heading(column_id, text=str(column), image=marker)
        self._marked_column_id = column_id
        self._marked_column_index = column_index

    def _split_chart(self, canvas: FigureCanvasTkAgg, axes_list: list[Any]) -> None:
        figure = canvas.figure
        line_groups = [list(axes.get_lines()) for axes in axes_list]
        line_groups = [lines for lines in line_groups if lines]
        if len(line_groups) < 2:
            lines = line_groups[0] if line_groups else []
            if len(lines) < 2:
                return
            midpoint = max(1, len(lines) // 2)
            line_groups = [lines[:midpoint], lines[midpoint:]]
        old_labels = [axes.get_ylabel() for axes in axes_list]
        figure.clear()
        split_axes = figure.subplots(len(line_groups), 1, sharex=True, squeeze=False).flatten().tolist()
        for index, (new_axes, lines) in enumerate(zip(split_axes, line_groups)):
            new_axes.set_facecolor("#FFFFFF")
            new_axes.grid(True, color="#DCE2E8", linewidth=0.7, alpha=0.8)
            new_axes.spines["top"].set_visible(False)
            new_axes.spines["right"].set_visible(False)
            new_axes.tick_params(colors="#344454", labelsize=9)
            new_axes.set_ylabel(old_labels[index] or (t("axis.primary") if index == 0 else t("axis.secondary")))
            new_handles = []
            for line in lines:
                new_line, = new_axes.plot(
                    line.get_xdata(),
                    line.get_ydata(),
                    label=line.get_label(),
                    color=line.get_color(),
                    linewidth=line.get_linewidth(),
                )
                if hasattr(line, "_campbell_row_indices"):
                    new_line._campbell_row_indices = line._campbell_row_indices
                    new_line._campbell_column = line._campbell_column
                new_handles.append(new_line)
            if new_handles:
                new_axes.legend(handles=new_handles, loc="upper left", bbox_to_anchor=(0, 1.0), ncol=min(4, len(new_handles)), frameon=False, fontsize=8)
        split_axes[-1].set_xlabel(t("axis.time"))
        axes_list[:] = split_axes

    def _use_same_axis(self, canvas: FigureCanvasTkAgg, axes_list: list[Any]) -> None:
        if len(axes_list) < 2:
            return
        lines = [line for axes in axes_list for line in axes.get_lines()]
        figure = canvas.figure
        figure.clear()
        target = figure.add_subplot(111)
        target.set_facecolor("#FFFFFF")
        target.grid(True, color="#DCE2E8", linewidth=0.7, alpha=0.8)
        target.spines["top"].set_visible(False)
        target.spines["right"].set_visible(False)
        target.tick_params(colors="#344454", labelsize=9)
        target.set_title(canvas._campbell_chart_spec["name"], loc="left", fontsize=13, fontweight="bold", color="#1F2D3A", pad=14)
        new_handles = []
        for line in lines:
            new_line, = target.plot(
                line.get_xdata(),
                line.get_ydata(),
                label=line.get_label(),
                color=line.get_color(),
                linewidth=line.get_linewidth(),
            )
            if hasattr(line, "_campbell_row_indices"):
                new_line._campbell_row_indices = line._campbell_row_indices
                new_line._campbell_column = line._campbell_column
            new_handles.append(new_line)
        target.set_xlabel(t("axis.time"))
        target.relim()
        target.set_autoscale_on(True)
        target.autoscale(enable=True, axis="both", tight=False)
        if len(new_handles) > 1:
            target.legend(handles=new_handles, loc="upper left", bbox_to_anchor=(0, 1.0), ncol=min(4, len(new_handles)), frameon=False, fontsize=8)
        axes_list[:] = [target]

    def _restore_chart(self, canvas: FigureCanvasTkAgg, axes_list: list[Any]) -> None:
        figure = canvas.figure
        spec = canvas._campbell_chart_spec
        headers = canvas._campbell_chart_headers
        figure.clear()
        axes = figure.add_subplot(111)
        axes.set_facecolor("#FFFFFF")
        axes.grid(True, color="#DCE2E8", linewidth=0.7, alpha=0.8)
        axes.spines["top"].set_visible(False)
        axes.spines["right"].set_visible(False)
        axes.tick_params(colors="#344454", labelsize=9)
        axes.set_title(spec["name"], loc="left", fontsize=13, fontweight="bold", color="#1F2D3A", pad=14)
        if spec["type"] == "windroos":
            restored_axes = [self._draw_wind_rose(figure, axes, spec, headers, self.chart_data)]
        elif spec["type"] == "scatter":
            self._draw_scatter(axes, spec, headers, self.chart_data)
            restored_axes = [axes]
        elif spec["type"] in {"bar_grouped", "bar_range", "bar_grouped_range"}:
            self._draw_special_bars(axes, spec, headers, self.chart_data)
            restored_axes = [axes]
        else:
            restored_axes = self._draw_lines(figure, axes, spec, headers, self.chart_data)
        axes_list[:] = restored_axes

    def _draw_special_bars(
        self, axes: Any, spec: dict[str, Any], headers: dict[str, str], data: pd.DataFrame,
    ) -> None:
        columns = spec["columns"]
        grouped_range = spec["type"] == "bar_grouped_range"
        if len(columns) < 2:
            raise ValueError(t("err.line_chart_needs_columns"))
        if spec["type"] == "bar_range" and len(columns) != 4:
            raise ValueError(t("chart.range_columns"))
        if grouped_range and (len(columns) - 1 < 6 or (len(columns) - 1) % 3):
            raise ValueError(t("chart.grouped_range_columns"))
        x_column, _ = _resolve_header(headers, columns[0])
        series: list[tuple[str, pd.DataFrame, pd.Series, pd.Series]] = []
        is_date: bool | None = None
        for configured in columns[1:]:
            column, secondary = _resolve_header(headers, configured)
            if secondary:
                raise ValueError(t("chart.bar_secondary"))
            period = self.pivot_columns.get(column)
            frame = self.pivot_frames[period] if period else data
            if period:
                x = pd.to_datetime(frame.iloc[:, 0], errors="coerce")
                dates = True
            else:
                x, dates = _x_values(data[x_column])
                parsed = pd.to_datetime(data[x_column].iloc[self.header_row_count:], format="mixed", errors="coerce")
                if len(parsed) and parsed.notna().sum() >= max(1, math.ceil(len(parsed) * 0.5)):
                    x = pd.to_datetime(data[x_column], format="mixed", errors="coerce")
                    dates = True
            if is_date is not None and dates != is_date:
                raise ValueError(t("pivot.chart_needs_time"))
            is_date = dates
            series.append((column, frame, x, pd.to_numeric(frame[column], errors="coerce")))

        if spec["type"] in {"bar_range", "bar_grouped_range"} and any(
            item[1] is not series[0][1] for item in series[1:]
        ):
            raise ValueError(t("chart.range_period"))
        positions: list[float] = []
        if grouped_range:
            for start in range(0, len(series), 3):
                group = series[start:start + 3]
                valid = group[0][2].notna() & group[0][3].notna() & group[1][3].notna() & group[2][3].notna()
                values = mdates.date2num(group[0][2][valid].to_numpy()) if is_date else group[0][2][valid].to_numpy()
                positions.extend(float(value) for value in values if math.isfinite(float(value)))
        else:
            for _, _, x, y in series:
                valid = x.notna() & y.notna()
                values = mdates.date2num(x[valid].to_numpy()) if is_date else x[valid].to_numpy()
                positions.extend(float(value) for value in values if math.isfinite(float(value)))
        unique = sorted(set(positions))
        if not unique:
            raise ValueError(t("err.no_plottable_data"))
        spacing = min((b - a for a, b in zip(unique, unique[1:])), default=1.0)
        width = spacing * 0.8
        if spec["type"] == "bar_range":
            frame = series[0][1]
            x = series[0][2]
            low, mean, high = (item[3] for item in series)
            valid = x.notna() & low.notna() & mean.notna() & high.notna()
            if not valid.any():
                raise ValueError(t("err.no_plottable_data"))
            invalid = valid & ((low > mean) | (mean > high))
            if invalid.any():
                position = int(invalid.to_numpy().nonzero()[0][0])
                raise ValueError(t(
                    "chart.range_order_details",
                    time=x.iloc[position],
                    minimum=series[0][0], min_value=f"{low.iloc[position]:.6g}",
                    mean=series[1][0], mean_value=f"{mean.iloc[position]:.6g}",
                    maximum=series[2][0], max_value=f"{high.iloc[position]:.6g}",
                ))
            xs = mdates.date2num(x[valid].to_numpy()) if is_date else x[valid].to_numpy()
            rows = list(frame.index[valid])
            lower = axes.bar(xs, mean[valid] - low[valid], bottom=low[valid], width=width,
                             color=LINE_COLORS[0], label=f"{series[0][0]} - {series[1][0]}")
            upper = axes.bar(xs, high[valid] - mean[valid], bottom=mean[valid], width=width,
                             color=LINE_COLORS[1], label=f"{series[1][0]} - {series[2][0]}")
            for patch, row in zip(lower, rows):
                patch.sticky_edges.y.clear()
                patch._campbell_target = (row, series[0][0])
            for patch, row in zip(upper, rows):
                patch.sticky_edges.y.clear()
                patch._campbell_target = (row, series[2][0])
            axes.scatter(xs, mean[valid], color=LINE_COLORS[2], marker="_", s=70, zorder=3, label=series[1][0])
            points = axes.collections[-1]
            points._campbell_row_indices = rows
            points._campbell_column = series[1][0]
            zero_line = axes.axhline(0, color="#263442", linewidth=1.5, zorder=5)
            zero_line._campbell_zero_line = True
            axes.autoscale_view(scalex=False, scaley=True)
        elif grouped_range:
            sensor_count = len(series) // 3
            group_width = width / sensor_count
            bar_width = group_width * 0.9
            for sensor_index in range(sensor_count):
                group = series[sensor_index * 3:sensor_index * 3 + 3]
                low, mean, high = (item[3] for item in group)
                x = group[0][2]
                valid = x.notna() & low.notna() & mean.notna() & high.notna()
                if not valid.any():
                    continue
                invalid = valid & ((low > mean) | (mean > high))
                if invalid.any():
                    position = int(invalid.to_numpy().nonzero()[0][0])
                    raise ValueError(t(
                        "chart.range_order_details",
                        time=x.iloc[position],
                        minimum=group[0][0], min_value=f"{low.iloc[position]:.6g}",
                        mean=group[1][0], mean_value=f"{mean.iloc[position]:.6g}",
                        maximum=group[2][0], max_value=f"{high.iloc[position]:.6g}",
                    ))
                xs = mdates.date2num(x[valid].to_numpy()) if is_date else x[valid].to_numpy()
                rows = list(group[0][1].index[valid])
                centers = xs + (sensor_index - (sensor_count - 1) / 2) * group_width
                lower = axes.bar(
                    centers, mean[valid] - low[valid], bottom=low[valid], width=bar_width,
                    color=LINE_COLORS[0], label=f"{group[0][0]} - {group[1][0]}",
                )
                upper = axes.bar(
                    centers, high[valid] - mean[valid], bottom=mean[valid], width=bar_width,
                    color=LINE_COLORS[1], label=f"{group[1][0]} - {group[2][0]}",
                )
                for patch, row in zip(lower, rows):
                    patch.sticky_edges.y.clear()
                    patch._campbell_target = (row, group[0][0])
                for patch, row in zip(upper, rows):
                    patch.sticky_edges.y.clear()
                    patch._campbell_target = (row, group[2][0])
                axes.scatter(
                    centers, mean[valid], color=LINE_COLORS[2], marker="_", s=70, zorder=3,
                    label=group[1][0],
                )
                points = axes.collections[-1]
                points._campbell_row_indices = rows
                points._campbell_column = group[1][0]
            zero_line = axes.axhline(0, color="#263442", linewidth=1.5, zorder=5)
            zero_line._campbell_zero_line = True
            axes.autoscale_view(scalex=False, scaley=True)
        else:
            count = len(series)
            for index, (column, frame, x, y) in enumerate(series):
                valid = x.notna() & y.notna()
                if not valid.any():
                    continue
                xs = mdates.date2num(x[valid].to_numpy()) if is_date else x[valid].to_numpy()
                offset = (index - (count - 1) / 2) * width / count
                bars = axes.bar(xs + offset, y[valid], width=width / count,
                                color=LINE_COLORS[index % len(LINE_COLORS)], label=column)
                for patch, row in zip(bars, frame.index[valid]):
                    patch._campbell_target = (row, column)
        axes.set_xlabel(x_column)
        axes.set_ylabel(t("axis.value"))
        if is_date:
            axes.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axes.xaxis.get_major_locator()))
        if spec["minimum"] is not None or spec["maximum"] is not None:
            axes.set_ylim(bottom=spec["minimum"], top=spec["maximum"])
        axes.legend(loc="upper left", ncol=min(4, len(series)), frameon=False, fontsize=8)

    def _draw_lines(
        self,
        figure: Figure,
        axes: Any,
        spec: dict[str, Any],
        headers: dict[str, str],
        data: pd.DataFrame,
    ) -> list[Any]:
        columns = spec["columns"]
        if len(columns) < 2:
            raise ValueError(t("err.line_chart_needs_columns"))
        x_column, _ = _resolve_header(headers, columns[0])
        x_values, is_date = _x_values(data[x_column])
        if not is_date and any(
            headers.get((name[:-10] if name.lower().endswith("@secondary") else name).strip().upper()) in self.pivot_columns
            for name in columns[1:]
        ):
            dates = pd.to_datetime(data[x_column], format="mixed", errors="coerce")
            measurements = dates.iloc[self.header_row_count:]
            if len(measurements) and measurements.notna().sum() >= max(1, math.ceil(len(measurements) * 0.5)):
                x_values, is_date = dates, True
        height_column: str | None = None
        height_values: list[Any] = []
        for configured_column in columns[1:]:
            try:
                candidate, _ = _resolve_header(headers, configured_column)
            except ValueError:
                continue
            if candidate.upper() == "HOOGTE":
                height_column = candidate
                height_values = (
                    data.iloc[self.header_row_count:][candidate]
                    .dropna()
                    .drop_duplicates()
                    .tolist()
                )
                if not height_values:
                    raise ValueError(t("err.height_empty"))
                break

        secondary_axes = None
        legend_handles = []
        missing_columns = []
        plotted = 0
        for series_index, configured_column in enumerate(columns[1:]):
            try:
                column, secondary = _resolve_header(headers, configured_column)
            except ValueError:
                missing_columns.append(configured_column.removesuffix("@secondary"))
                continue
            if column.upper() == "HOOGTE":
                continue
            pivot_period = self.pivot_columns.get(column)
            series_data = self.pivot_frames[pivot_period] if pivot_period else data
            if pivot_period:
                if not is_date:
                    raise ValueError(t("pivot.chart_needs_time"))
                series_x = pd.to_datetime(series_data.iloc[:, 0], errors="coerce")
            else:
                series_x = x_values
            y_values = pd.to_numeric(series_data[column], errors="coerce")
            target = axes
            if secondary:
                if secondary_axes is None:
                    secondary_axes = axes.twinx()
                    secondary_axes.spines["top"].set_visible(False)
                    secondary_axes.tick_params(colors="#344454", labelsize=9)
                target = secondary_axes
            chart_heights = (height_values or [None]) if not pivot_period else [None]
            for height_index, height_value in enumerate(chart_heights):
                valid = series_x.notna() & y_values.notna()
                label = column
                if height_column is not None and height_value is not None:
                    matches_height = data[height_column].eq(height_value).fillna(False)
                    if self.header_row_count:
                        matches_height.iloc[:self.header_row_count] = False
                    valid &= matches_height
                    label = f"{column} (Hoogte {height_value})"
                if not valid.any():
                    continue
                color_index = series_index * len(chart_heights) + height_index
                color = LINE_COLORS[color_index % len(LINE_COLORS)]
                if spec["type"] == "bar":
                    xs = series_x[valid]
                    step = xs.diff().median()
                    if is_date:
                        width = step if pd.notna(step) and step else pd.Timedelta(days=1)
                        width = mdates.date2num(xs.iloc[0] + width) - mdates.date2num(xs.iloc[0])
                    else:
                        width = step if pd.notna(step) and step else 1
                    line = target.bar(xs, y_values[valid], width=width * 0.8, color=color, label=label)
                else:
                    line, = target.plot(
                        series_x[valid],
                        y_values[valid],
                        color=color,
                        linewidth=2.4 if secondary else 0.9,
                        label=label,
                    )
                line._campbell_row_indices = list(series_data.index[valid])
                line._campbell_column = column
                legend_handles.append(line)
                plotted += 1
        if not plotted:
            raise ValueError(t("err.no_plottable_data"))
        axes.set_ylabel(t("axis.value"), color="#344454")
        if secondary_axes is not None:
            secondary_axes.set_ylabel(t("axis.secondary"), color="#344454")
        minimum, maximum = spec["minimum"], spec["maximum"]
        if minimum is not None or maximum is not None:
            axes.set_ylim(bottom=minimum, top=maximum)
        if is_date:
            axes.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axes.xaxis.get_major_locator()))
        axes.set_xlabel(x_column, color="#344454")
        if len(legend_handles) > 1:
            axes.legend(handles=legend_handles, loc="upper left", bbox_to_anchor=(0, 1.0), ncol=min(4, len(legend_handles)), frameon=False, fontsize=8)
        if missing_columns:
            axes.text(
                0.01,
                0.01,
                t("chart.skipped_columns", columns=", ".join(missing_columns)),
                transform=axes.transAxes,
                fontsize=8,
                color="#8A5A00",
                va="bottom",
            )
        axes.set_xlim(auto=True)
        return [axes] + ([secondary_axes] if secondary_axes is not None else [])

    def _draw_scatter(
        self,
        axes: Any,
        spec: dict[str, Any],
        headers: dict[str, str],
        data: pd.DataFrame,
    ) -> None:
        if len(spec["columns"]) != 2:
            raise ValueError(t("err.scatter_needs_two"))
        y_column, _ = _resolve_header(headers, spec["columns"][0])
        x_column, _ = _resolve_header(headers, spec["columns"][1])
        x_period = self.pivot_columns.get(x_column)
        y_period = self.pivot_columns.get(y_column)
        x_frame = self.pivot_frames[x_period] if x_period else data
        y_frame = self.pivot_frames[y_period] if y_period else data
        if x_frame is y_frame:
            x_values = pd.to_numeric(x_frame[x_column], errors="coerce")
            y_values = pd.to_numeric(y_frame[y_column], errors="coerce")
            row_indices = y_frame.index
        else:
            x_times = pd.to_datetime(x_frame.iloc[:, 0], format="mixed", errors="coerce")
            y_times = pd.to_datetime(y_frame.iloc[:, 0], format="mixed", errors="coerce")
            x_points = pd.DataFrame({"time": x_times, "x": pd.to_numeric(x_frame[x_column], errors="coerce")}).dropna(subset=["time"])
            y_points = pd.DataFrame({"time": y_times, "y": pd.to_numeric(y_frame[y_column], errors="coerce"), "row": y_frame.index}).dropna(subset=["time"])
            matched = y_points.merge(x_points, on="time", how="inner")
            x_values, y_values, row_indices = matched["x"], matched["y"], matched["row"]
        valid = x_values.notna() & y_values.notna() & np.isfinite(x_values) & np.isfinite(y_values)
        points = axes.scatter(x_values[valid], y_values[valid], s=10, alpha=0.55, color=LINE_COLORS[0], edgecolors="none")
        points._campbell_row_indices = list(row_indices[valid])
        points._campbell_column = y_column
        axes.set_xlabel(x_column)
        axes.set_ylabel(y_column)
        axes.grid(True, color="#DCE2E8", linewidth=0.7)
        if not valid.any():
            raise ValueError(t("err.scatter_no_numeric"))
        if spec.get("trend") == "linear":
            x = x_values[valid].to_numpy(dtype=float)
            y = y_values[valid].to_numpy(dtype=float)
            if len(x) < 2 or np.all(x == x[0]):
                axes.text(0.02, 0.98, t("chart.trend_insufficient"), transform=axes.transAxes,
                          va="top", fontsize=9, color="#8A5A00")
                return
            centered_x = x - x.mean()
            centered_y = y - y.mean()
            slope = float(np.dot(centered_x, centered_y) / np.dot(centered_x, centered_x))
            intercept = float(y.mean() - slope * x.mean())
            xs = np.array([x.min(), x.max()])
            axes.plot(xs, slope * xs + intercept, color="#C00000", linewidth=1.5)
            residuals = y - (slope * x + intercept)
            total = float(np.dot(centered_y, centered_y))
            r2 = 1 - float(np.dot(residuals, residuals)) / total if total else None
            equation = f"y = {slope:.6g}x {intercept:+.6g}"
            r2_text = f"{r2:.6g}" if r2 is not None else t("chart.trend_undefined")
            axes.text(0.02, 0.98, f"{equation}\nR² = {r2_text}", transform=axes.transAxes,
                      va="top", fontsize=9, color="#7B0000",
                      bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"})

    def _aligned_wind(self, data: pd.DataFrame, direction: str, value: str) -> pd.DataFrame:
        """Pair direction and value on a common time base; either may be a raw or a pivot column."""
        direction_period = self.pivot_columns.get(direction)
        value_period = self.pivot_columns.get(value)
        if direction_period == value_period:
            frame = self.pivot_frames[direction_period] if direction_period else data
            if value not in frame.columns:
                raise ValueError(t("err.column_not_in_data", name=value))
            pair = pd.DataFrame({
                "direction": pd.to_numeric(frame[direction], errors="coerce").mod(360),
                "value": pd.to_numeric(frame[value], errors="coerce"),
            })
            return pair.dropna()
        if direction_period and value_period:
            raise ValueError(t("err.wind_period_mismatch"))
        raw = data.iloc[self.header_row_count:]
        raw_times = pd.to_datetime(raw.iloc[:, 0], format="mixed", errors="coerce")
        if direction_period is None:
            radians = np.deg2rad(pd.to_numeric(raw[direction], errors="coerce"))
            stamps = period_start(raw_times, value_period)
            parts = pd.DataFrame({"sin": np.sin(radians), "cos": np.cos(radians)}).groupby(stamps).mean()
            mean_direction = np.rad2deg(np.arctan2(parts["sin"], parts["cos"])).mod(360)
            frame = self.pivot_frames[value_period]
            times = pd.to_datetime(frame.iloc[:, 0], errors="coerce")
            pair = pd.DataFrame({
                "direction": mean_direction.reindex(times).to_numpy(),
                "value": pd.to_numeric(frame[value], errors="coerce").to_numpy(),
            })
            return pair.dropna()
        frame = self.pivot_frames[direction_period]
        times = pd.to_datetime(frame.iloc[:, 0], errors="coerce")
        directions = pd.Series(pd.to_numeric(frame[direction], errors="coerce").mod(360).to_numpy(), index=times)
        stamps = period_start(raw_times, direction_period)
        pair = pd.DataFrame({
            "direction": directions.reindex(stamps).to_numpy(),
            "value": pd.to_numeric(raw[value], errors="coerce").to_numpy(),
        })
        return pair.dropna()

    def _draw_wind_rose(
        self,
        figure: Figure,
        axes: Any,
        spec: dict[str, Any],
        headers: dict[str, str],
        data: pd.DataFrame,
    ) -> Any:
        if not spec["columns"]:
            raise ValueError(t("err.no_wind_column"))
        column, _ = _resolve_header(headers, spec["columns"][0])
        period = self.pivot_columns.get(column)
        frame = self.pivot_frames[period] if period else data
        directions = pd.to_numeric(frame[column], errors="coerce").dropna().mod(360)
        if directions.empty:
            raise ValueError(t("err.no_wind_values"))
        bins = 16
        width = 360 / bins
        labels = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        angles = [index * math.tau / bins for index in range(bins)]
        concentration_names = [name.strip() for name in spec["columns"][1:] if name.strip()]
        if concentration_names:
            figure.delaxes(axes)
            polar_axes = figure.add_subplot(111, projection="polar")
            polar_axes.set_theta_zero_location("N")
            polar_axes.set_theta_direction(-1)
            bar_width = math.tau / bins * 0.9
            colors = ["#178B83", "#C2410C", "#1D4E89", "#7A3E9D", "#4D7C0F", "#B91C1C"]
            series_means = []
            for number, configured in enumerate(concentration_names):
                name, _ = _resolve_header(headers, configured)
                pair = self._aligned_wind(data, column, name)
                sector = ((pair["direction"] + width / 2) // width).mod(bins)
                means = pair["value"].groupby(sector).mean().reindex(range(bins))
                if means.notna().sum() == 0:
                    raise ValueError(t("err.no_wind_values"))
                series_means.append((number, name, means))
            # Grouped bars: each series gets its own slot, the group stays centred on the compass direction.
            slot = bar_width / len(series_means)
            for number, name, means in series_means:
                offsets = [angle + (number - (len(series_means) - 1) / 2) * slot for angle in angles]
                polar_axes.bar(
                    offsets, means.fillna(0).to_numpy(), width=slot, label=name,
                    color=colors[number % len(colors)], edgecolor="white", linewidth=0.5,
                )
            polar_axes.set_xticks(angles)
            polar_axes.set_xticklabels(labels)
            polar_axes.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1))
            polar_axes.set_title(spec["name"], loc="left", fontsize=13, fontweight="bold", color="#1F2D3A", pad=22)
            polar_axes.set_ylabel(t("axis.mean_concentration"), labelpad=28)
            return polar_axes
        counts = [0] * bins
        for direction in directions:
            index = int((float(direction) + width / 2) // width) % bins
            counts[index] += 1
        percentages = [count / len(directions) * 100 for count in counts]
        figure.delaxes(axes)
        polar_axes = figure.add_subplot(111, projection="polar")
        polar_axes.set_theta_zero_location("N")
        polar_axes.set_theta_direction(-1)
        angles = [index * math.tau / bins for index in range(bins)]
        polar_axes.bar(angles, percentages, width=math.tau / bins * 0.9, color="#178B83", edgecolor="white", linewidth=1)
        polar_axes.set_xticks(angles)
        polar_axes.set_xticklabels(["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"])
        polar_axes.set_title(spec["name"], loc="left", fontsize=13, fontweight="bold", color="#1F2D3A", pad=22)
        polar_axes.set_ylabel(t("axis.percentage"), labelpad=28)
        return polar_axes

    def _on_auto_scale_toggle(self) -> None:
        enabled = self.auto_scale_y.get()
        set_auto_scale_y(enabled)
        for canvas, axes_list, is_wind_rose in self.chart_axes:
            if is_wind_rose:
                continue
            if enabled:
                self._fit_y_to_view(canvas, axes_list)
            else:
                self._reset_y_scale(canvas, axes_list)
            canvas.draw_idle()

    def _reset_y_scale(self, canvas: FigureCanvasTkAgg, axes_list: list[Any]) -> None:
        spec = getattr(canvas, "_campbell_chart_spec", {})
        for index, axes in enumerate(axes_list):
            axes.relim()
            axes.set_autoscaley_on(True)
            axes.autoscale_view(scalex=False, scaley=True)
            if index == 0 and (spec.get("minimum") is not None or spec.get("maximum") is not None):
                axes.set_ylim(bottom=spec.get("minimum"), top=spec.get("maximum"))

    def _fit_y_to_view(self, canvas: FigureCanvasTkAgg, axes_list: list[Any]) -> None:
        spec = getattr(canvas, "_campbell_chart_spec", {})
        fixed_primary = spec.get("minimum") is not None or spec.get("maximum") is not None
        for index, axes in enumerate(axes_list):
            if index == 0 and fixed_primary:
                continue
            left, right = axes.get_xlim()
            low, high = math.inf, -math.inf
            for line in axes.get_lines():
                if getattr(line, "_campbell_zero_line", False):
                    continue
                points = line.get_xydata()
                if not len(points):
                    continue
                inside = (points[:, 0] >= left) & (points[:, 0] <= right)
                values = points[inside, 1]
                values = values[~(values != values)]
                if len(values):
                    low, high = min(low, float(values.min())), max(high, float(values.max()))
            for patch in axes.patches:
                if hasattr(patch, "_campbell_target"):
                    x = patch.get_x()
                    if x + patch.get_width() >= left and x <= right:
                        bottom = patch.get_y()
                        top = bottom + patch.get_height()
                        low, high = min(low, bottom, top), max(high, bottom, top)
            for points in axes.collections:
                if hasattr(points, "_campbell_row_indices"):
                    offsets = points.get_offsets()
                    if len(offsets):
                        visible = offsets[(offsets[:, 0] >= left) & (offsets[:, 0] <= right), 1]
                        visible = visible[np.isfinite(visible)]
                        if len(visible):
                            low, high = min(low, float(visible.min())), max(high, float(visible.max()))
            if spec.get("type") == "bar_range" and math.isfinite(low):
                low, high = min(low, 0), max(high, 0)
            if not math.isfinite(low):
                continue
            margin = (high - low) * 0.05 or abs(high) * 0.05 or 1.0
            axes.set_ylim(low - margin, high + margin)

    def _on_scroll(self, event: Any, canvas: FigureCanvasTkAgg, axes_list: list[Any], is_wind_rose: bool) -> None:
        if is_wind_rose:
            return
        main_axes = axes_list[0]
        notches = abs(float(event.step or 1))
        if event.inaxes in axes_list:
            left, right = main_axes.get_xlim()
            span = right - left
            if not span:
                return
            anchor = event.xdata
            if anchor is None:
                anchor = (left + right) / 2
            fraction = min(1.0, max(0.0, (anchor - left) / span))
            factor = 0.8 ** notches if event.step > 0 else 1 / (0.8 ** notches)
            new_span = span * factor
            minimum, maximum = main_axes.dataLim.xmin, main_axes.dataLim.xmax
            if math.isfinite(minimum) and math.isfinite(maximum):
                # Nooit verder uitzoomen dan de volledige dataspanning, anders ontstaan lege vlakken.
                new_span = min(new_span, maximum - minimum)
            new_left = anchor - fraction * new_span
            new_right = new_left + new_span
            if math.isfinite(minimum) and math.isfinite(maximum):
                if new_left < minimum:
                    new_right += minimum - new_left
                    new_left = minimum
                if new_right > maximum:
                    new_left -= new_right - maximum
                    new_right = maximum
                new_left = max(new_left, minimum)
            for axes in axes_list:
                axes.set_xlim(new_left, new_right)
        else:
            if notches == 0:
                return
            left, right = main_axes.get_xlim()
            span = right - left
            minimum, maximum = main_axes.dataLim.xmin, main_axes.dataLim.xmax
            if not span or not math.isfinite(minimum) or not math.isfinite(maximum):
                return
            # Buiten de plot schuift omhoog/omlaag met het wiel naar links/rechts.
            direction = -1 if event.step > 0 else 1
            offset = span * 0.2 * notches * direction
            new_left = max(minimum, min(left + offset, maximum - span))
            new_right = new_left + span
            for axes in axes_list:
                axes.set_xlim(new_left, new_right)
        if self.auto_scale_y.get():
            self._fit_y_to_view(canvas, axes_list)
        canvas.draw_idle()

    def _open_logdef_editor(self) -> None:
        from logdef_editor import LogdefEditor
        editor = LogdefEditor(
            self.root,
            [str(c) for c in self.chart_data.columns] + list(self.pivot_columns),
            self.logger,
            LOGDEF_DIR,
            location=self.location,
            on_saved=self._reload_logdef,
        )
        editor.iconbitmap(str(application_icon_path()))

    def _reload_logdef(self, path: Path) -> None:
        active = chart_config_path(self.logger, LOGDEF_DIR, self.location)
        if active is None or active.resolve() != path.resolve():
            messagebox.showwarning(
                t("logdef.title_new"),
                t("logdef.saved_not_active", name=path.name),
                parent=self.root,
            )
            return
        self._finish_cell_edit(save=True)
        try:
            self._reload_formulas()
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror(t("logdef.title_new"), t("logdef.reload_failed", error=exc), parent=self.root)
            return
        self._data_changed = False
        self.status.set(t("status.saved", name=path.name))

    def _open_modelling(self) -> None:
        try:
            from addons.scientific_modelling.modelling_gui import open_modelling_window
            window = open_modelling_window(
                self.root, self.chart_data, self.header_row_count, self.source.name, CONFIGS_DIR / "Modeldefs",
            )
            if window is not None:
                try:
                    window.iconbitmap(str(application_icon_path()))
                except tk.TclError:
                    pass
                window.deiconify()
                window.lift()
                window.focus_force()
        except Exception as error:
            logging.getLogger(__name__).exception("Modelling window failed")
            messagebox.showerror(t("toolbar.modelling"), f"{type(error).__name__}: {error}", parent=self.root)

    def _open_pivot_editor(self) -> None:
        from pivot_editor import PivotEditor
        editor = PivotEditor(
            self.root, self.chart_data, self.header_row_count,
            pivot_path(self.logger, self.location, PIVOT_DIR), self._reload_formulas,
        )
        if editor.winfo_exists():
            editor.iconbitmap(str(application_icon_path()))

    def _open_pivot_formulas(self) -> None:
        index = self.pivot_choice.current()
        if index < 0:
            messagebox.showinfo(t("pivot.formulas"), t("pivot.empty"), parent=self.root)
            return
        from formula_editor import FormulaEditor
        period = self.pivot_periods[index]
        editor = FormulaEditor(
            self.root, self.pivot_bases[period], 0, self.logger,
            PIVOT_FORMULA_DIR / period, location=self.location,
            on_saved=self._reload_formulas, function_dir=FUNCTION_DIR,
        )
        editor.title(f"{t('pivot.formulas')} - {t('pivot.period.' + period)}")
        editor.iconbitmap(str(application_icon_path()))

    def _open_formula_editor(self) -> None:
        from formula_editor import FormulaEditor
        editor = FormulaEditor(
            self.root,
            self.data,
            self.header_row_count,
            self.logger,
            FORMULA_DIR,
            location=self.location,
            on_saved=self._reload_formulas,
            function_dir=FUNCTION_DIR,
        )
        editor.iconbitmap(str(application_icon_path()))

    def _reload_formulas(self) -> None:
        old_tabs = self.notebook.tabs()
        selected = self.notebook.select()
        tab_index = old_tabs.index(selected) if selected in old_tabs else 0
        choice = self.pivot_choice.current()
        period = self.pivot_periods[choice] if choice >= 0 else None
        self._populate_tabs()
        if period in self.pivot_periods:
            self.pivot_choice.current(self.pivot_periods.index(period))
            self._change_pivot_period()
        tabs = self.notebook.tabs()
        self.notebook.select(tabs[min(tab_index, len(tabs) - 1)])
        self._last_selected_tab = self.notebook.select()

    def _open_help(self) -> None:
        try:
            help_file = helpdef_path()
            help_text = help_file.read_text(encoding="utf-8").strip()
            if not help_text:
                raise ValueError(t("err.help_empty", path=help_file))
            with Image.open(HELP_IMAGE_PATH) as source_image:
                help_image = ImageOps.contain(
                    source_image.convert("RGB"),
                    (460, 620),
                    method=Image.Resampling.LANCZOS,
                )
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            messagebox.showerror(t("dialog.help_unavailable"), str(exc), parent=self.root)
            return

        window = tk.Toplevel(self.root)
        window.title(t("help.window_title"))
        window.iconbitmap(str(application_icon_path()))
        window.geometry("1080x740")
        window.minsize(800, 560)
        window.transient(self.root)
        window.grab_set()

        content = ttk.Frame(window, padding=16)
        content.pack(fill="both", expand=True)
        image_panel = ttk.Frame(content)
        image_panel.pack(side="left", fill="y", padx=(0, 16))
        photo = ImageTk.PhotoImage(help_image, master=window)
        image_label = ttk.Label(image_panel, image=photo)
        image_label.image = photo
        image_label.pack(anchor="n")

        text_panel = ttk.Frame(content)
        text_panel.pack(side="left", fill="both", expand=True)
        text_panel.rowconfigure(0, weight=1)
        text_panel.columnconfigure(0, weight=1)
        help_widget = tk.Text(
            text_panel,
            wrap="word",
            font=("Segoe UI", 10),
            padx=12,
            pady=12,
            relief="flat",
            background="#FFFFFF",
        )
        help_widget.tag_configure("title", font=("Segoe UI", 16, "bold"), spacing3=12)
        help_widget.tag_configure("heading", font=("Segoe UI", 11, "bold"), spacing1=12, spacing3=4)
        scrollbar = ttk.Scrollbar(text_panel, orient="vertical", command=help_widget.yview)
        help_widget.configure(yscrollcommand=scrollbar.set)
        help_widget.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        for line in help_text.splitlines():
            if line.startswith("# "):
                help_widget.insert("end", line[2:] + "\n", "title")
            elif line.startswith("## "):
                help_widget.insert("end", line[3:] + "\n", "heading")
            else:
                help_widget.insert("end", line + "\n")
        help_widget.configure(state="disabled")

        ttk.Button(content, text=t("button.close"), command=window.destroy).pack(side="bottom", anchor="e", pady=(12, 0))

    def _manage_columns(self) -> None:
        names = [str(c) for c in self.data.columns]
        window = tk.Toplevel(self.root)
        window.title(t("dialog.manage_columns"))
        window.transient(self.root)
        window.geometry("360x520")
        buttons = ttk.Frame(window, padding=8)
        buttons.pack(side="bottom", fill="x")
        side = ttk.Frame(window, padding=8)
        side.pack(side="right", fill="y")
        listbox = tk.Listbox(window, selectmode="extended", exportselection=False)
        listbox.pack(side="left", fill="both", expand=True)
        for name in names[1:]:
            listbox.insert("end", name)

        def move(step: int) -> None:
            selection = list(listbox.curselection())
            if not selection:
                return
            order = range(len(selection)) if step < 0 else range(len(selection) - 1, -1, -1)
            for i in order:
                index = selection[i]
                target = index + step
                if target < 0 or target >= listbox.size() or target in selection:
                    continue
                text = listbox.get(index)
                listbox.delete(index)
                listbox.insert(target, text)
                selection[i] = target
            listbox.selection_clear(0, "end")
            for index in selection:
                listbox.selection_set(index)

        def delete() -> None:
            for index in reversed(listbox.curselection()):
                listbox.delete(index)

        def apply() -> None:
            order = [names[0]] + list(listbox.get(0, "end"))
            if order == names:
                window.destroy()
                return
            removed = len(names) - len(order)
            self.removed_columns |= {n.strip().casefold() for n in names if n not in order}
            attrs = dict(self.data.attrs)
            self.data = self.data[order].copy()
            self.data.attrs.update(attrs)
            self._data_changed = True
            window.destroy()
            self.status.set(t("status.columns_managed", removed=removed))
            self._populate_tabs()
            self._last_selected_tab = self.notebook.select()

        ttk.Button(side, text=t("button.move_up"), command=lambda: move(-1)).pack(fill="x", pady=2)
        ttk.Button(side, text=t("button.move_down"), command=lambda: move(1)).pack(fill="x", pady=2)
        ttk.Button(side, text=t("button.delete"), command=delete).pack(fill="x", pady=(12, 2))
        ttk.Button(
            side, text=t("button.add_columns"),
            command=lambda: self._add_columns_from_file(
                parent=window, on_added=lambda: (window.destroy(), self._manage_columns())
            ),
        ).pack(fill="x", pady=(12, 2))
        ttk.Button(buttons, text=t("button.close"), command=window.destroy).pack(side="right")
        ttk.Button(buttons, text=t("button.apply"), command=apply).pack(side="right", padx=(0, 8))
        window.grab_set()

    def _add_columns_from_file(self, parent=None, on_added=None) -> None:
        parent = parent or self.root
        selected = filedialog.askopenfilename(
            parent=parent,
            title=t("dialog.choose_extra_dat"),
            filetypes=[(t("filetype.data"), "*.dat *.csv *.txt"), (t("filetype.all"), "*.*")],
            initialdir=str(self.source.parent),
        )
        if not selected:
            return
        source = Path(selected)
        try:
            extra = read_dat_file(source)
            extra_headers = int(extra.attrs.get("header_row_count", 0))
            prefix = logger_from_filename(source)
        except Exception as exc:
            messagebox.showerror("Campbell Sci", str(exc), parent=parent)
            return
        available = [str(c) for c in extra.columns[1:]]
        window = tk.Toplevel(parent)
        window.title(t("dialog.add_columns", name=source.name))
        window.transient(self.root)
        window.geometry("360x460")
        listbox = tk.Listbox(window, selectmode="extended", exportselection=False)
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        buttons = ttk.Frame(window, padding=8)
        buttons.pack(side="bottom", fill="x")
        scrollbar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)
        for column in available:
            listbox.insert("end", column)

        def apply() -> None:
            chosen = [available[i] for i in listbox.curselection()]
            if not chosen:
                return
            try:
                merged, added = merge_columns_by_time(
                    self.data, self.header_row_count, extra, extra_headers, chosen, prefix,
                )
            except Exception as exc:
                window.destroy()
                messagebox.showerror("Campbell Sci", str(exc), parent=parent)
                return
            window.destroy()
            self.data = merged
            self.removed_columns -= {str(a).strip().casefold() for a in added}
            self._data_changed = True
            self.status.set(t("status.columns_added", count=len(added), name=source.name))
            if on_added:
                on_added()
            self._populate_tabs()
            self._last_selected_tab = self.notebook.select()

        ttk.Button(buttons, text=t("button.close"), command=window.destroy).pack(side="right")
        ttk.Button(buttons, text=t("button.add_columns"), command=apply).pack(side="right", padx=(0, 8))
        window.grab_set()

    def _choose_another_file(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            title=t("dialog.choose_dat"),
            filetypes=[(t("filetype.data"), "*.dat *.csv *.txt"), (t("filetype.all"), "*.*")],
            initialdir=get_last_dir(),
        )
        if not selected:
            return
        source = Path(selected)
        set_last_dir(source.parent)
        try:
            data = read_dat_file(source)
            logger = logger_from_filename(source)
            location = logger_and_location_from_filename(source)[1]
            if logger in {"BM", "BRAS"}:
                data = data.replace(["6999", "-6999", "7999", "-7999", "NAN", "INF"], float("nan"))
            self.data, self.source, self.logger, self.location, self.page = data, source, logger, location, 0
            self.data2_page = 0
            self.removed_columns = set()
            self._data_changed = False
            self.root.title(f"Campbell Sci | {source.name}")
            self.file_label.configure(text=source.name)
            self.status.set(t("status.rows_logger", rows=f"{len(data):,}", logger=logger))
            self._populate_tabs()
            self._last_selected_tab = self.notebook.select()
        except Exception as exc:
            messagebox.showerror("Campbell Sci", str(exc), parent=self.root)
