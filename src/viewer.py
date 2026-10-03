from __future__ import annotations

import csv
import math
from pathlib import Path
import re
from tkinter import ttk
from typing import Any

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import pandas as pd
from settings import get_auto_scale_y, get_last_dir, set_auto_scale_y, set_last_dir
import tkinter as tk
from tkinter import filedialog, messagebox

PAGE_SIZE = 1000
HEADER_ROWS = 2
LINE_COLORS = ["#1F4E78", "#C00000", "#548235", "#BF8F00", "#7030A0", "#2E75B6", "#833C00"]
CONFIG_DIR = Path(__file__).with_name("graph_configs")
MENU_DIR = CONFIG_DIR


def read_dat_file(file_path: str | Path) -> pd.DataFrame:
    path = Path(file_path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.strip().upper().startswith('"TIMESTAMP"') or line.strip().upper().startswith("TIMESTAMP")),
        None,
    )
    if header_index is None:
        raise ValueError(f"Geen Campbell TOA5-header gevonden in {path.name}")
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
        raise ValueError(f"Geen meetgegevens gevonden in {path.name}")
    data.columns = [str(column).strip() for column in data.columns]
    return data


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
    header_index = next(
        (index for index, line in enumerate(lines) if line.strip().upper().startswith('"TIMESTAMP"') or line.strip().upper().startswith("TIMESTAMP")),
        None,
    )
    return lines[:header_index] if header_index else []


def load_chart_config(logger: str, config_dir: str | Path = CONFIG_DIR) -> list[dict[str, Any]]:
    path = Path(config_dir) / f"{logger.upper()}.logdef"
    if not path.is_file() and logger.upper() == "BRAS":
        path = Path(config_dir) / "BM.logdef"
    if not path.is_file():
        return []
    charts = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split(";")]
        if len(fields) != 7:
            raise ValueError(f"Ongeldige configuratie in {path.name}:{line_number}")
        name, chart_type, columns, minimum, maximum, number_format, tab_color = fields
        charts.append({
            "name": name,
            "type": chart_type.lower(),
            "columns": [column.strip() for column in columns.split(",") if column.strip()],
            "minimum": float(minimum) if minimum else None,
            "maximum": float(maximum) if maximum else None,
            "number_format": number_format or "0.0",
            "tab_color": tab_color,
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
            raise ValueError(f"Ongeldige menuconfiguratie in {path.name}:{line_number}")
        label, action = fields
        items.append({"label": label, "action": action})
    return items


def _header_lookup(data: pd.DataFrame) -> dict[str, str]:
    return {str(column).strip().upper(): str(column) for column in data.columns}


def _resolve_header(headers: dict[str, str], configured: str) -> tuple[str, bool]:
    secondary = configured.lower().endswith("@secondary")
    name = configured[:-10].strip() if secondary else configured.strip()
    column = headers.get(name.upper())
    if column is None:
        raise ValueError(f"Kolom '{name}' staat niet in de data.")
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


def _logger_from_path(path: Path) -> str:
    match = re.search(r"\d{4}_\d{2}_\d{2}(?:_\d{2}(?:_\d{2}(?:_\d{2})?)?)?$", path.stem)
    return path.stem[:match.start()].rstrip("_").upper() if match else path.stem.split("_")[0].upper()


def _x_values(series: pd.Series) -> tuple[pd.Series, bool]:
    dates = pd.to_datetime(series, format="mixed", errors="coerce")
    if dates.notna().sum() >= max(2, int(len(series) * 0.5)):
        return dates, True
    numbers = pd.to_numeric(series, errors="coerce")
    if numbers.notna().sum() >= max(2, int(len(series) * 0.5)):
        return numbers, False
    return pd.Series(range(len(series)), index=series.index), False


class CampbellViewer:
    def __init__(self, root: tk.Tk, data: pd.DataFrame, source: Path, logger: str):
        self.root = root
        self.data = data
        self.source = source
        self.logger = logger
        self.page = 0
        self._data_changed = False
        self.figures: list[Figure] = []
        self.canvases: list[FigureCanvasTkAgg] = []
        self.chart_axes: list[tuple[FigureCanvasTkAgg, list[Any], bool]] = []
        self._chart_origin_tab: str | None = None
        self._configure_window()
        self._build_shell()
        self._populate_tabs()
        self._last_selected_tab = self.notebook.select()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

    def _configure_window(self) -> None:
        self.root.title(f"Campbell Sci | {self.source.name}")
        self.root.geometry("1380x860")
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
        style.configure("TNotebook.Tab", padding=(14, 8), font=("Segoe UI", 9))
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 9), background="#FFFFFF", fieldbackground="#FFFFFF")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_shell(self) -> None:
        toolbar = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(14, 10))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Open .dat", command=self._choose_another_file).pack(side="left")
        ttk.Button(toolbar, text="Nieuwe .logdef", command=self._open_logdef_editor).pack(side="left", padx=(8, 0))
        self.auto_scale_y = tk.BooleanVar(value=get_auto_scale_y())
        ttk.Checkbutton(
            toolbar, text="Y-as automatisch schalen bij zoomen", variable=self.auto_scale_y,
            command=self._on_auto_scale_toggle,
        ).pack(side="left", padx=(14, 0))
        self.file_label = ttk.Label(toolbar, text=self.source.name, style="Toolbar.TLabel")
        self.file_label.pack(side="left", padx=(14, 0))
        self.status = tk.StringVar(value=f"{len(self.data):,} rijen | logger {self.logger}")
        ttk.Label(toolbar, textvariable=self.status, style="Toolbar.TLabel").pack(side="right")
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _populate_tabs(self) -> None:
        self._clear_tabs()
        self._add_data_tab()
        specs = load_chart_config(self.logger)
        if not specs:
            specs = _fallback_charts(self.data)
        if not specs:
            empty = ttk.Frame(self.notebook, padding=24)
            ttk.Label(empty, text="Geen numerieke meetkolommen om te tekenen.").pack(anchor="w")
            self.notebook.add(empty, text="Grafieken")
            return
        headers = _header_lookup(self.data)
        used_names: set[str] = set()
        for index, spec in enumerate(specs):
            name = spec["name"] or f"Grafiek {index + 1}"
            if name in used_names:
                name = f"{name[:26]} ({index + 1})"
            used_names.add(name)
            chart_spec = dict(spec)
            chart_spec["name"] = name
            page = ttk.Frame(self.notebook)
            self.notebook.add(page, text=name)
            try:
                self._draw_chart(page, chart_spec, headers, self.data)
            except (KeyError, ValueError) as exc:
                ttk.Label(page, text=f"Grafiek kan niet worden opgebouwd: {exc}", padding=24).pack(anchor="w")
        if len(self.notebook.tabs()) > 1:
            self.notebook.select(self.notebook.tabs()[1])

    def _clear_tabs(self) -> None:
        for tab in self.notebook.tabs():
            self.notebook.forget(tab)
        for figure in self.figures:
            plt.close(figure)
        self.figures.clear()
        self.canvases.clear()
        self.chart_axes.clear()

    def _on_notebook_tab_changed(self, _event: tk.Event) -> None:
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
        for canvas, axes_list, _is_wind_rose in self.chart_axes:
            self._restore_chart(canvas, axes_list)
            canvas.draw_idle()
        self._data_changed = False

    def _add_data_tab(self) -> None:
        page = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(page, text="Data")
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)
        columns = [str(column) for column in self.data.columns]
        self.header_row_count = HEADER_ROWS if len(self.data) > HEADER_ROWS else 0
        frozen_table = ttk.Treeview(
            page, columns=columns, show="headings", height=self.header_row_count, selectmode="none"
        )
        table = ttk.Treeview(page, columns=columns, show="")
        ybar = ttk.Scrollbar(page, orient="vertical", command=lambda *args: self._scroll_table(table, "y", *args))
        xbar = ttk.Scrollbar(page, orient="horizontal", command=lambda *args: self._scroll_table(table, "x", *args))
        table.configure(yscrollcommand=ybar.set, xscrollcommand=lambda first, last: self._on_table_xscroll(xbar, frozen_table, first, last))
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
        ttk.Button(controls, text="Vorige", command=lambda: self._change_page(-1)).pack(side="left")
        ttk.Label(controls, textvariable=self.page_label).pack(side="left", padx=12)
        ttk.Button(controls, text="Volgende", command=lambda: self._change_page(1)).pack(side="left")
        ttk.Button(controls, text="Opslaan", command=self._save_data).pack(side="right")
        self.table = table
        self.frozen_table = frozen_table
        self._marked_column_id: str | None = None
        self._marked_column_index: int | None = None
        table.bind("<Double-1>", self._begin_cell_edit)
        self._edit_entry: tk.Entry | None = None
        self._add_spreadsheet_menu(table)
        self._show_page()

    def _on_table_xscroll(self, xbar: ttk.Scrollbar, frozen_table: ttk.Treeview, first: str, last: str) -> None:
        xbar.set(first, last)
        frozen_table.xview_moveto(float(first))

    def _sync_frozen_widths(self, frozen_table: ttk.Treeview, table: ttk.Treeview) -> None:
        for column in frozen_table["columns"]:
            table.column(column, width=frozen_table.column(column, "width"))


    def _row_position(self, item: str) -> int:
        return self.header_row_count + self.page * PAGE_SIZE + self.table.index(item)


    def _begin_cell_edit(self, event: tk.Event) -> None:
        table = self.table
        item = table.identify_row(event.y)
        column_id = table.identify_column(event.x)
        if not item or not column_id:
            return
        self._start_cell_edit(item, column_id)

    def _start_cell_edit(self, item: str, column_id: str) -> None:
        table = self.table
        if column_id != self._marked_column_id or item not in table.selection():
            return
        self._finish_cell_edit(save=True)
        box = table.bbox(item, column_id)
        if not box:
            return
        x, y, width, height = box
        column_index = int(column_id.replace("#", "")) - 1
        current_value = table.set(item, column_id)
        entry = tk.Entry(table)
        entry.insert(0, current_value)
        entry.select_range(0, "end")
        entry.place(x=x, y=y, width=width, height=height)
        entry.focus_set()
        entry.bind("<Return>", lambda _event: self._finish_cell_edit(save=True))
        entry.bind("<KP_Enter>", lambda _event: self._finish_cell_edit(save=True))
        entry.bind("<Escape>", lambda _event: self._finish_cell_edit(save=False))
        entry.bind("<FocusOut>", lambda _event: self._finish_cell_edit(save=True))
        self._edit_entry = entry
        self._edit_target = (item, column_id, column_index)

    def _finish_cell_edit(self, save: bool) -> None:
        entry = self._edit_entry
        if entry is None:
            return
        self._edit_entry = None
        item, column_id, column_index = self._edit_target
        new_text = entry.get()
        entry.destroy()
        if not save:
            return
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
        self.status.set(f"Cel aangepast: rij {row_position + 1}, kolom {column}")

    def _save_data(self) -> None:
        original_extension = self.source.suffix or ".dat"
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Sla gewijzigde data op",
            initialdir=str(self.source.parent),
            initialfile=f"{self.source.stem}_bewerkt{original_extension}",
            defaultextension=original_extension,
            filetypes=[(f"Campbell data (*{original_extension})", f"*{original_extension}"), ("Alle bestanden", "*.*")],
        )
        if not target:
            return
        try:
            preamble = _read_preamble_lines(self.source)
            with open(target, "w", encoding="utf-8", newline="") as handle:
                for line in preamble:
                    handle.write(line + "\n")
                writer = csv.writer(handle, quoting=csv.QUOTE_NONNUMERIC)
                writer.writerow(list(self.data.columns))
                header_rows = self.data.iloc[: self.header_row_count]
                data_rows = self.data.iloc[self.header_row_count :]
                for row in header_rows.itertuples(index=False, name=None):
                    writer.writerow([_format_toa5_cell(value, "") for value in row])
                for row in data_rows.itertuples(index=False, name=None):
                    writer.writerow([_format_toa5_cell(value, "NAN") for value in row])
            self.status.set(f"Opgeslagen: {Path(target).name}")
        except Exception as exc:
            messagebox.showerror("Opslaan mislukt", str(exc), parent=self.root)



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
        if self._marked_column_index is None or self._marked_column_id is None:
            messagebox.showinfo(
                "Campbell Sci",
                "Ga eerst via de grafiek naar een punt om de kolom te markeren.",
                parent=self.root,
            )
            return
        selected_items = self.table.selection()
        if not selected_items:
            messagebox.showinfo(
                "Campbell Sci",
                "Selecteer eerst de rijen die u wilt wissen (klik en Shift-klik of Ctrl-klik).",
                parent=self.root,
            )
            return
        column = self.data.columns[self._marked_column_index]
        confirmed = messagebox.askyesno(
            "Campbell Sci",
            f"{len(selected_items)} cel(len) wissen in gemarkeerde kolom '{column}'?",
            parent=self.root,
        )
        if not confirmed:
            return
        for item in selected_items:
            row_position = self._row_position(item)
            self.data.iloc[row_position, self._marked_column_index] = float("nan")
            self.table.set(item, self._marked_column_id, "")
        self._data_changed = True
        self.status.set(f"{len(selected_items)} cellen gewist in kolom {column}")


    def _show_page(self) -> None:
        self.table.delete(*self.table.get_children())
        measurement_count = len(self.data) - self.header_row_count
        start = self.header_row_count + self.page * PAGE_SIZE
        end = min(len(self.data), start + PAGE_SIZE)
        for row in self.data.iloc[start:end].itertuples(index=False, name=None):
            values = ["" if pd.isna(value) else str(value) for value in row]
            self.table.insert("", "end", values=values)
        total_pages = max(1, math.ceil(measurement_count / PAGE_SIZE))
        self.page_label.set(
            f"Rijen {start - self.header_row_count + 1:,}-{end - self.header_row_count:,} van {measurement_count:,} | pagina {self.page + 1} van {total_pages}"
        )

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
        else:
            axes_list = self._draw_lines(figure, axes, spec, headers, data)
            is_wind_rose = False

        canvas = FigureCanvasTkAgg(figure, master=page)
        canvas_widget = canvas.get_tk_widget()
        canvas_widget.pack(fill="both", expand=True, padx=10, pady=10)
        canvas.draw()
        canvas.mpl_connect("scroll_event", lambda event, target=canvas, chart_axes=axes_list, polar=is_wind_rose: self._on_scroll(event, target, chart_axes, polar))
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
        for axes in axes_list:
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
        return nearest if nearest_distance <= 225 else None

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
        try:
            row_position = self.data.index.get_loc(row_index)
        except KeyError:
            return
        measurement_position = int(row_position) - self.header_row_count
        if measurement_position < 0:
            return
        self.page = measurement_position // PAGE_SIZE
        self._show_page()
        self.notebook.select(self.notebook.tabs()[0])
        item = self.table.get_children()[measurement_position % PAGE_SIZE]
        self.table.selection_set(item)
        self.table.focus(item)
        column_index = list(self.data.columns).index(column)
        self._center_on_cell(item, column_index)
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
            new_axes.set_ylabel(old_labels[index] or ("Primaire as" if index == 0 else "Secundaire as"))
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
        split_axes[-1].set_xlabel("Tijd")
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
        target.set_xlabel("Tijd")
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
            restored_axes = [self._draw_wind_rose(figure, axes, spec, headers, self.data)]
        elif spec["type"] == "scatter":
            self._draw_scatter(axes, spec, headers, self.data)
            restored_axes = [axes]
        else:
            restored_axes = self._draw_lines(figure, axes, spec, headers, self.data)
        axes_list[:] = restored_axes

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
            raise ValueError("Een lijngrafiek vereist een tijdkolom en minstens een meetkolom.")
        x_column, _ = _resolve_header(headers, columns[0])
        x_values, is_date = _x_values(data[x_column])
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
                    raise ValueError("De kolom 'Hoogte' bevat geen waarden om op te splitsen.")
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
            y_values = pd.to_numeric(data[column], errors="coerce")
            target = axes
            if secondary:
                if secondary_axes is None:
                    secondary_axes = axes.twinx()
                    secondary_axes.spines["top"].set_visible(False)
                    secondary_axes.tick_params(colors="#344454", labelsize=9)
                target = secondary_axes
            chart_heights = height_values or [None]
            for height_index, height_value in enumerate(chart_heights):
                valid = x_values.notna() & y_values.notna()
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
                    xs = x_values[valid]
                    step = xs.diff().median()
                    width = step if pd.notna(step) and step else 1
                    if is_date:
                        width = mdates.date2num(xs.iloc[0] + width) - mdates.date2num(xs.iloc[0])
                    line = target.bar(xs, y_values[valid], width=width * 0.8, color=color, label=label)
                else:
                    line, = target.plot(
                        x_values[valid],
                        y_values[valid],
                        color=color,
                        linewidth=2.4 if secondary else 0.9,
                        label=label,
                    )
                line._campbell_row_indices = list(data.index[valid])
                line._campbell_column = column
                legend_handles.append(line)
                plotted += 1
        if not plotted:
            raise ValueError("De geconfigureerde kolommen bevatten geen plotbare data.")
        axes.set_ylabel("Waarde", color="#344454")
        if secondary_axes is not None:
            secondary_axes.set_ylabel("Secundaire as", color="#344454")
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
                f"Overgeslagen, niet in deze export: {', '.join(missing_columns)}",
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
            raise ValueError("Een scattergrafiek vereist exact twee kolommen: Y en X.")
        y_column, _ = _resolve_header(headers, spec["columns"][0])
        x_column, _ = _resolve_header(headers, spec["columns"][1])
        x_values = pd.to_numeric(data[x_column], errors="coerce")
        y_values = pd.to_numeric(data[y_column], errors="coerce")
        valid = x_values.notna() & y_values.notna()
        points = axes.scatter(x_values[valid], y_values[valid], s=10, alpha=0.55, color=LINE_COLORS[0], edgecolors="none")
        points._campbell_row_indices = list(data.index[valid])
        points._campbell_column = y_column
        axes.set_xlabel(x_column)
        axes.set_ylabel(y_column)
        axes.grid(True, color="#DCE2E8", linewidth=0.7)
        if not valid.any():
            raise ValueError("De scatterkolommen bevatten geen numerieke datapunten.")

    def _draw_wind_rose(
        self,
        figure: Figure,
        axes: Any,
        spec: dict[str, Any],
        headers: dict[str, str],
        data: pd.DataFrame,
    ) -> Any:
        if not spec["columns"]:
            raise ValueError("Geen windrichtingskolom opgegeven.")
        column, _ = _resolve_header(headers, spec["columns"][0])
        directions = pd.to_numeric(data[column], errors="coerce").dropna().mod(360)
        if directions.empty:
            raise ValueError("Geen numerieke windrichtingen gevonden.")
        bins = 16
        width = 360 / bins
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
        polar_axes.set_ylabel("Percentage", labelpad=28)
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
                points = line.get_xydata()
                if not len(points):
                    continue
                inside = (points[:, 0] >= left) & (points[:, 0] <= right)
                values = points[inside, 1]
                values = values[~(values != values)]
                if len(values):
                    low, high = min(low, float(values.min())), max(high, float(values.max()))
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
        LogdefEditor(self.root, [str(c) for c in self.data.columns], self.logger, CONFIG_DIR)

    def _choose_another_file(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Kies een Campbell .dat-bestand",
            filetypes=[("Campbell data", "*.dat"), ("Alle bestanden", "*.*")],
            initialdir=get_last_dir(),
        )
        if not selected:
            return
        source = Path(selected)
        set_last_dir(source.parent)
        try:
            data = read_dat_file(source)
            logger = _logger_from_path(source)
            if logger in {"BM", "BRAS"}:
                data = data.replace(["6999", "-6999", "7999", "-7999", "NAN", "INF"], float("nan"))
                if "IR_R_R_Avg" in data.columns:
                    infrared = pd.to_numeric(data["IR_R_R_Avg"], errors="coerce")
                    data["Leaf T."] = (infrared / 0.0000000567) ** 0.25 - 273.16
            self.data, self.source, self.logger, self.page = data, source, logger, 0
            self._data_changed = False
            self.root.title(f"Campbell Sci | {source.name}")
            self.file_label.configure(text=source.name)
            self.status.set(f"{len(data):,} rijen | logger {logger}")
            self._populate_tabs()
            self._last_selected_tab = self.notebook.select()
        except Exception as exc:
            messagebox.showerror("Campbell Sci", str(exc), parent=self.root)
