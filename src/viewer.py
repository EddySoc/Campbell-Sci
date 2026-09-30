from __future__ import annotations

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
import tkinter as tk
from tkinter import filedialog, messagebox

PAGE_SIZE = 1000
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
        self.figures: list[Figure] = []
        self.canvases: list[FigureCanvasTkAgg] = []
        self.chart_axes: list[tuple[FigureCanvasTkAgg, list[Any], bool]] = []
        self._chart_origin_tab: str | None = None
        self._configure_window()
        self._build_shell()
        self._populate_tabs()

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
            page = ttk.Frame(self.notebook)
            self.notebook.add(page, text=name)
            try:
                self._draw_chart(page, spec, headers)
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

    def _add_data_tab(self) -> None:
        page = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(page, text="Data")
        page.rowconfigure(0, weight=1)
        page.columnconfigure(0, weight=1)
        columns = [str(column) for column in self.data.columns]
        table = ttk.Treeview(page, columns=columns, show="headings")
        ybar = ttk.Scrollbar(page, orient="vertical", command=lambda *args: self._scroll_table(table, "y", *args))
        xbar = ttk.Scrollbar(page, orient="horizontal", command=lambda *args: self._scroll_table(table, "x", *args))
        table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        table.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        for column in columns:
            table.heading(column, text=column)
            table.column(column, width=max(105, min(220, len(column) * 9)), minwidth=80, stretch=False)
        controls = ttk.Frame(page)
        controls.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.page_label = tk.StringVar()
        ttk.Button(controls, text="Vorige", command=lambda: self._change_page(-1)).pack(side="left")
        ttk.Label(controls, textvariable=self.page_label).pack(side="left", padx=12)
        ttk.Button(controls, text="Volgende", command=lambda: self._change_page(1)).pack(side="left")
        ttk.Button(controls, text="Opslaan", command=self._save_data).pack(side="right")
        self.table = table
        self._cell_highlight = tk.Label(table, background="#FFE58A", foreground="#1F2D3A", anchor="w", padx=4)
        self._cell_highlight.place_forget()
        table.bind("<Configure>", lambda _event: self._reposition_cell_highlight())
        table.bind("<MouseWheel>", lambda _event: self._reposition_cell_highlight())
        self._cell_highlight.bind("<MouseWheel>", lambda _event: self._reposition_cell_highlight())
        self._add_spreadsheet_menu(table)
        self._show_page()

    def _save_data(self) -> None:
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Sla gewijzigde data op",
            initialdir=str(self.source.parent),
            initialfile=f"{self.source.stem}_bewerkt.csv",
            defaultextension=".csv",
            filetypes=[("CSV-bestand", "*.csv"), ("Alle bestanden", "*.*")],
        )
        if not target:
            return
        try:
            self.data.to_csv(target, index=False)
            self.status.set(f"Opgeslagen: {Path(target).name}")
        except Exception as exc:
            messagebox.showerror("Opslaan mislukt", str(exc), parent=self.root)

    def _scroll_table(self, table: ttk.Treeview, axis: str, *args: Any) -> None:
        if axis == "x":
            table.xview(*args)
        else:
            table.yview(*args)
        table.after_idle(self._reposition_cell_highlight)

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
        if action != "return_to_chart":
            return
        if self._chart_origin_tab in self.notebook.tabs():
            self.notebook.select(self._chart_origin_tab)
        elif len(self.notebook.tabs()) > 1:
            self.notebook.select(self.notebook.tabs()[1])

    def _show_page(self) -> None:
        self.table.delete(*self.table.get_children())
        start = self.page * PAGE_SIZE
        end = min(len(self.data), start + PAGE_SIZE)
        for row in self.data.iloc[start:end].itertuples(index=False, name=None):
            values = ["" if pd.isna(value) else str(value) for value in row]
            self.table.insert("", "end", values=values)
        total_pages = max(1, math.ceil(len(self.data) / PAGE_SIZE))
        self.page_label.set(f"Rijen {start + 1:,}-{end:,} van {len(self.data):,} | pagina {self.page + 1} van {total_pages}")

    def _change_page(self, direction: int) -> None:
        total_pages = max(1, math.ceil(len(self.data) / PAGE_SIZE))
        self.page = max(0, min(total_pages - 1, self.page + direction))
        self._show_page()

    def _draw_chart(self, page: ttk.Frame, spec: dict[str, Any], headers: dict[str, str]) -> None:
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
            axes = self._draw_wind_rose(figure, axes, spec, headers)
            axes_list = [axes]
            is_wind_rose = True
        elif chart_type == "scatter":
            self._draw_scatter(axes, spec, headers)
            axes_list = [axes]
            is_wind_rose = False
        else:
            axes_list = self._draw_lines(figure, axes, spec, headers)
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
        self.page = int(row_position) // PAGE_SIZE
        self._show_page()
        self.notebook.select(self.notebook.tabs()[0])
        item = self.table.get_children()[int(row_position) % PAGE_SIZE]
        self.table.selection_set(item)
        self.table.focus(item)
        self.table.see(item)
        column_index = list(self.data.columns).index(column)
        if column_index:
            self.table.xview_moveto(column_index / max(1, len(self.data.columns) - 1))
        self._highlight_cell(item, column_index, str(self.data.iloc[int(row_position), column_index]))

    def _highlight_cell(self, item: str, column_index: int, value: str) -> None:
        column_id = f"#{column_index + 1}"
        self._highlighted_cell = (item, column_id, value)
        self._reposition_cell_highlight()

    def _reposition_cell_highlight(self) -> None:
        highlighted = getattr(self, "_highlighted_cell", None)
        if highlighted is None:
            return
        item, column_id, value = highlighted
        box = self.table.bbox(item, column_id)
        if not box:
            self._cell_highlight.place_forget()
            return
        x, y, width, height = box
        self._cell_highlight.configure(text=value)
        self._cell_highlight.place(x=x, y=y, width=width, height=height)

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
        target.set_xlabel("Tijd")
        target.relim()
        target.set_autoscale_on(True)
        target.autoscale(enable=True, axis="both", tight=False)
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
            restored_axes = [self._draw_wind_rose(figure, axes, spec, headers)]
        elif spec["type"] == "scatter":
            self._draw_scatter(axes, spec, headers)
            restored_axes = [axes]
        else:
            restored_axes = self._draw_lines(figure, axes, spec, headers)
        axes_list[:] = restored_axes

    def _draw_lines(self, figure: Figure, axes: Any, spec: dict[str, Any], headers: dict[str, str]) -> list[Any]:
        columns = spec["columns"]
        if len(columns) < 2:
            raise ValueError("Een lijngrafiek vereist een tijdkolom en minstens een meetkolom.")
        x_column, _ = _resolve_header(headers, columns[0])
        x_values, is_date = _x_values(self.data[x_column])
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
            y_values = pd.to_numeric(self.data[column], errors="coerce")
            valid = x_values.notna() & y_values.notna()
            if not valid.any():
                continue
            target = axes
            if secondary:
                if secondary_axes is None:
                    secondary_axes = axes.twinx()
                    secondary_axes.spines["top"].set_visible(False)
                    secondary_axes.tick_params(colors="#344454", labelsize=9)
                target = secondary_axes
            line, = target.plot(
                x_values[valid],
                y_values[valid],
                color=LINE_COLORS[series_index % len(LINE_COLORS)],
                linewidth=2.4 if secondary else 0.9,
                label=column,
            )
            line._campbell_row_indices = list(self.data.index[valid])
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

    def _draw_scatter(self, axes: Any, spec: dict[str, Any], headers: dict[str, str]) -> None:
        if len(spec["columns"]) != 2:
            raise ValueError("Een scattergrafiek vereist exact twee kolommen: Y en X.")
        y_column, _ = _resolve_header(headers, spec["columns"][0])
        x_column, _ = _resolve_header(headers, spec["columns"][1])
        x_values = pd.to_numeric(self.data[x_column], errors="coerce")
        y_values = pd.to_numeric(self.data[y_column], errors="coerce")
        valid = x_values.notna() & y_values.notna()
        points = axes.scatter(x_values[valid], y_values[valid], s=10, alpha=0.55, color=LINE_COLORS[0], edgecolors="none")
        points._campbell_row_indices = list(self.data.index[valid])
        points._campbell_column = y_column
        axes.set_xlabel(x_column)
        axes.set_ylabel(y_column)
        axes.grid(True, color="#DCE2E8", linewidth=0.7)
        if not valid.any():
            raise ValueError("De scatterkolommen bevatten geen numerieke datapunten.")

    def _draw_wind_rose(self, figure: Figure, axes: Any, spec: dict[str, Any], headers: dict[str, str]) -> Any:
        if not spec["columns"]:
            raise ValueError("Geen windrichtingskolom opgegeven.")
        column, _ = _resolve_header(headers, spec["columns"][0])
        directions = pd.to_numeric(self.data[column], errors="coerce").dropna().mod(360)
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
            new_left = anchor - fraction * new_span
            new_right = new_left + new_span
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
        canvas.draw_idle()

    def _choose_another_file(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Kies een Campbell .dat-bestand",
            filetypes=[("Campbell data", "*.dat"), ("Alle bestanden", "*.*")],
        )
        if not selected:
            return
        source = Path(selected)
        try:
            data = read_dat_file(source)
            logger = _logger_from_path(source)
            if logger in {"BM", "BRAS"}:
                data = data.replace(["6999", "-6999", "7999", "-7999", "NAN", "INF"], float("nan"))
                if "IR_R_R_Avg" in data.columns:
                    infrared = pd.to_numeric(data["IR_R_R_Avg"], errors="coerce")
                    data["Leaf T."] = (infrared / 0.0000000567) ** 0.25 - 273.16
            self.data, self.source, self.logger, self.page = data, source, logger, 0
            self.root.title(f"Campbell Sci | {source.name}")
            self.file_label.configure(text=source.name)
            self.status.set(f"{len(data):,} rijen | logger {logger}")
            self._populate_tabs()
        except Exception as exc:
            messagebox.showerror("Campbell Sci", str(exc), parent=self.root)
