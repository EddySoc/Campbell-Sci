from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from formulas import backup_original
from i18n import t
from logdef_format import parse_chart_fields

CHART_TYPES = ["line", "bar", "bar_grouped", "bar_range", "scatter", "windroos"]
TIME_CHART_TYPES = {"line", "bar", "bar_grouped", "bar_range"}
COLORS = ["", "red", "blue", "cyan", "yellow", "green", "orange", "purple", "gray", "black"]
HEADER = (
    "# name;type;columns;minimum;maximum;number format;tab color\n"
    "# type=windroos uses one direction-column header in columns; omit the line for loggers without a windrose.\n"
    "# tab color: hex code or a readable color name\n"
    "# optional eighth field: linear (scatter trendline)\n"
)


def new_chart(index: int) -> dict:
    return {"name": t("chart.default_name", number=index), "type": "line", "columns": [], "minimum": "", "maximum": "", "format": "0.0", "color": ""}


def chart_to_line(chart: dict, time_column: str) -> str:
    columns = list(chart["columns"])
    # Lijn- en staafgrafieken gebruiken de tijdkolom als x-as.
    if chart["type"] in TIME_CHART_TYPES and not any(c.split("@")[0] == time_column for c in columns):
        columns.insert(0, time_column)
    fields = [chart["name"], chart["type"], ",".join(columns), chart["minimum"], chart["maximum"], chart["format"], chart["color"]]
    if chart["type"] == "scatter" and chart.get("trend") == "linear":
        fields.append("linear")
    return ";".join(str(f).replace(";", ",").strip() for f in fields)


class LogdefEditor(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        headers: list[str],
        logger: str,
        config_dir: Path,
        location: str | None = None,
        on_saved: Callable[[Path], None] | None = None,
    ):
        super().__init__(parent)
        self.title(t("logdef.title_new"))
        self.geometry("980x600")
        self.headers = headers
        self.time_column = headers[0] if headers else "TIMESTAMP"
        self.logger = logger
        self.location = location
        self.config_dir = Path(config_dir)
        self.on_saved = on_saved
        self.charts: list[dict] = []
        self.current: int | None = None
        self.loaded_path = self._find_existing()
        self._build()
        charts = self._read_charts(self.loaded_path) if self.loaded_path else None
        if charts:
            self._set_charts(charts)
            if self.loaded_path is not None:
                self.title(t("logdef.title_edit", name=self.loaded_path.name))
        else:
            self.loaded_path = None
            self._add_chart()

    def _find_existing(self) -> Path | None:
        names = []
        if self.location:
            names.append(f"{self.logger.upper()}_{self.location.upper()}.logdef")
        names.append(f"{self.logger.upper()}.logdef")
        if self.logger.upper() == "BRAS":
            names.append("BM.logdef")
        return next((self.config_dir / n for n in names if (self.config_dir / n).is_file()), None)

    def _build(self) -> None:
        left = ttk.Frame(self, padding=8)
        left.pack(side="left", fill="y")
        ttk.Label(left, text=t("logdef.charts")).pack(anchor="w")
        self.chart_list = tk.Listbox(left, width=24, exportselection=False)
        self.chart_list.pack(fill="y", expand=True)
        self.chart_list.bind("<<ListboxSelect>>", self._on_select)
        for text, command in [(t("button.add"), self._add_chart), (t("button.remove"), self._remove_chart),
                              (t("button.up"), lambda: self._move(-1)), (t("button.down"), lambda: self._move(1))]:
            ttk.Button(left, text=text, command=command).pack(fill="x", pady=1)

        right = ttk.Frame(self, padding=8)
        right.pack(side="left", fill="both", expand=True)
        form = ttk.Frame(right)
        form.pack(fill="x")
        self.name_var = tk.StringVar()
        self.type_var = tk.StringVar(value="line")
        self.type_labels = {kind: t(f"logdef.type.{kind}", default=kind) for kind in CHART_TYPES}
        self.min_var = tk.StringVar()
        self.max_var = tk.StringVar()
        self.format_var = tk.StringVar(value="0.0")
        self.color_var = tk.StringVar()
        self.trend_var = tk.BooleanVar(value=False)
        rows = [(t("logdef.name"), ttk.Entry(form, textvariable=self.name_var)),
                (t("logdef.type"), ttk.Combobox(form, textvariable=self.type_var, values=list(self.type_labels.values()), state="readonly")),
                (t("logdef.minimum"), ttk.Entry(form, textvariable=self.min_var)),
                (t("logdef.maximum"), ttk.Entry(form, textvariable=self.max_var)),
                (t("logdef.number_format"), ttk.Entry(form, textvariable=self.format_var)),
                (t("logdef.tab_color"), ttk.Combobox(form, textvariable=self.color_var, values=COLORS))]
        for row, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
            widget.grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        self.trend_button = ttk.Checkbutton(form, text=t("logdef.trend_linear"), variable=self.trend_var)
        self.trend_button.grid(row=len(rows), column=1, sticky="w", padx=6, pady=2)
        form.columnconfigure(1, weight=1)
        rows[1][1].bind("<<ComboboxSelected>>", lambda _e: self._update_hint())

        pick = ttk.Frame(right)
        pick.pack(fill="both", expand=True, pady=(10, 0))
        avail = ttk.Frame(pick)
        avail.pack(side="left", fill="both", expand=True)
        ttk.Label(avail, text=t("logdef.dat_columns")).pack(anchor="w")
        self.available = tk.Listbox(avail, selectmode="extended", exportselection=False)
        self.available.pack(fill="both", expand=True)
        for header in self.headers:
            self.available.insert("end", header)

        buttons = ttk.Frame(pick, padding=6)
        buttons.pack(side="left")
        self.primary_button = ttk.Button(buttons, text=t("logdef.primary"), command=lambda: self._assign(False))
        self.primary_button.pack(fill="x", pady=2)
        self.secondary_button = ttk.Button(buttons, text=t("logdef.secondary"), command=lambda: self._assign(True))
        self.secondary_button.pack(fill="x", pady=2)
        ttk.Button(buttons, text=t("logdef.unassign"), command=self._unassign).pack(fill="x", pady=2)

        chosen = ttk.Frame(pick)
        chosen.pack(side="left", fill="both", expand=True)
        self.hint = tk.StringVar()
        ttk.Label(chosen, textvariable=self.hint).pack(anchor="w")
        self.selected = tk.Listbox(chosen, selectmode="extended", exportselection=False)
        self.selected.pack(fill="both", expand=True)
        reorder = ttk.Frame(chosen)
        reorder.pack(fill="x")
        ttk.Button(reorder, text=t("button.up"), command=lambda: self._move_selected(-1)).pack(side="left")
        ttk.Button(reorder, text=t("button.down"), command=lambda: self._move_selected(1)).pack(side="left", padx=(4, 0))

        bottom = ttk.Frame(right)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text=t("logdef.load"), command=self._open).pack(side="left")
        ttk.Button(bottom, text=t("logdef.save_as"), command=lambda: self._save(save_as=True)).pack(side="right")
        ttk.Button(bottom, text=t("button.save"), command=self._save).pack(side="right", padx=(0, 8))

    def _update_hint(self) -> None:
        kind = self._chart_type()
        self.secondary_button.configure(state="disabled" if kind == "scatter" else "normal")
        self.primary_button.configure(text=t("logdef.assign") if kind == "scatter" else t("logdef.primary"))
        self.trend_button.configure(state="normal" if kind == "scatter" else "disabled")
        self.hint.set(t({
            "line": "logdef.hint_time",
            "bar": "logdef.hint_time",
            "bar_grouped": "logdef.hint_grouped",
            "bar_range": "logdef.hint_range",
            "scatter": "logdef.hint_scatter",
            "windroos": "logdef.hint_windrose",
        }[kind]))

    def _chart_type(self) -> str:
        return next((kind for kind, label in self.type_labels.items() if label == self.type_var.get()), self.type_var.get())

    def _commit(self) -> None:
        if self.current is None:
            return
        chart = self.charts[self.current]
        chart.update(name=self.name_var.get().strip() or t("chart.default_name", number=self.current + 1), type=self._chart_type(),
                     minimum=self.min_var.get().strip(), maximum=self.max_var.get().strip(),
                     format=self.format_var.get().strip(), color=self.color_var.get().strip(),
                     columns=list(self.selected.get(0, "end")),
                     trend="linear" if self._chart_type() == "scatter" and self.trend_var.get() else "")
        self.chart_list.delete(self.current)
        self.chart_list.insert(self.current, chart["name"])

    def _load(self, index: int) -> None:
        self.current = index
        chart = self.charts[index]
        self.name_var.set(chart["name"])
        kind = str(chart["type"])
        self.type_var.set(self.type_labels.get(kind, kind))
        self.trend_var.set(chart.get("trend") == "linear")
        self.min_var.set(chart["minimum"])
        self.max_var.set(chart["maximum"])
        self.format_var.set(chart["format"])
        self.color_var.set(chart["color"])
        self.selected.delete(0, "end")
        for column in chart["columns"]:
            self.selected.insert("end", column)
        self._update_hint()
        self.chart_list.selection_clear(0, "end")
        self.chart_list.selection_set(index)

    def _on_select(self, _event: object) -> None:
        selection = self.chart_list.curselection()
        if selection and selection[0] != self.current:
            self._commit()
            self._load(selection[0])

    def _add_chart(self) -> None:
        self._commit()
        self.charts.append(new_chart(len(self.charts) + 1))
        self.chart_list.insert("end", self.charts[-1]["name"])
        self._load(len(self.charts) - 1)

    def _remove_chart(self) -> None:
        if self.current is None:
            return
        del self.charts[self.current]
        self.chart_list.delete(self.current)
        self.current = None
        if self.charts:
            self._load(min(len(self.charts) - 1, self.chart_list.size() - 1))
        else:
            self._add_chart()

    def _move(self, step: int) -> None:
        if self.current is None:
            return
        self._commit()
        target = self.current + step
        if not 0 <= target < len(self.charts):
            return
        self.charts[self.current], self.charts[target] = self.charts[target], self.charts[self.current]
        names = [chart["name"] for chart in self.charts]
        self.chart_list.delete(0, "end")
        for name in names:
            self.chart_list.insert("end", name)
        self._load(target)

    def _assign(self, secondary: bool) -> None:
        if secondary and self._chart_type() == "scatter":
            return
        existing = {c.split("@")[0] for c in self.selected.get(0, "end")}
        single = self._chart_type() == "windroos"
        for index in self.available.curselection():
            header = self.headers[index]
            if header in existing:
                continue
            if single:
                self.selected.delete(0, "end")
            self.selected.insert("end", f"{header}@secondary" if secondary and self._chart_type() == "line" else header)
            existing.add(header)

    def _unassign(self) -> None:
        for index in reversed(self.selected.curselection()):
            self.selected.delete(index)

    def _move_selected(self, step: int) -> None:
        selected = set(self.selected.curselection())
        if not selected:
            return
        columns = list(self.selected.get(0, "end"))
        for index in sorted(selected, reverse=step > 0):
            target = index + step
            if 0 <= target < len(columns) and target not in selected:
                columns[index], columns[target] = columns[target], columns[index]
                selected.remove(index)
                selected.add(target)
        self.selected.delete(0, "end")
        for column in columns:
            self.selected.insert("end", column)
        for index in sorted(selected):
            self.selected.selection_set(index)
        self.selected.see(min(selected) if step < 0 else max(selected))

    def _open(self) -> None:
        config_name = f"{self.logger}_{self.location}" if self.location else self.logger
        path = filedialog.askopenfilename(
            parent=self, title=t("logdef.open_title"), initialdir=self.config_dir,
            initialfile=f"{config_name}.logdef", filetypes=[("Logdef", "*.logdef")])
        if not path:
            return
        charts = self._read_charts(Path(path))
        if charts is None:
            return
        if not charts:
            messagebox.showwarning("Logdef", t("logdef.no_charts"), parent=self)
            return
        self.loaded_path = Path(path)
        self._set_charts(charts)

    def _read_charts(self, path: Path) -> list[dict] | None:
        charts = []
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                fields = parse_chart_fields(line, path.name, number)
            except ValueError:
                messagebox.showerror("Logdef", t("logdef.invalid_line", number=number, name=path.name), parent=self)
                return None
            name, kind, columns, minimum, maximum, fmt, color = fields[:7]
            cols = [c.strip() for c in columns.split(",") if c.strip()]
            # De tijdkolom wordt bij opslaan weer automatisch toegevoegd.
            if kind.lower() in TIME_CHART_TYPES and cols and cols[0].upper() == self.time_column.upper():
                cols = cols[1:]
            charts.append({"name": name, "type": kind.lower(), "columns": cols, "minimum": minimum,
                           "maximum": maximum, "format": fmt, "color": color,
                           "trend": fields[7] if len(fields) == 8 else ""})
        return charts

    def _set_charts(self, charts: list[dict]) -> None:
        self.charts = charts
        self.current = None
        self.chart_list.delete(0, "end")
        for chart in charts:
            self.chart_list.insert("end", chart["name"])
        self._load(0)

    def _save(self, save_as: bool = False) -> None:
        self._commit()
        problems = []
        charts = []
        for chart in self.charts:
            if not chart["columns"]:
                continue
            needed = 2 if chart["type"] == "scatter" else 1
            if len(chart["columns"]) < needed:
                problems.append(t("logdef.min_columns", name=chart["name"], needed=needed))
            if chart["type"] == "scatter" and len(chart["columns"]) != 2:
                problems.append(t("logdef.scatter_columns", name=chart["name"]))
            if chart["type"] == "bar_range" and len(chart["columns"]) != 3:
                problems.append(t("logdef.range_columns", name=chart["name"]))
            if chart["type"] in {"bar_range", "bar_grouped"} and any(
                column.split("@")[0].strip().upper() == self.time_column.upper()
                for column in chart["columns"]
            ):
                problems.append(t("logdef.auto_time", name=chart["name"]))
            charts.append(chart)
        if problems:
            messagebox.showwarning("Logdef", "\n".join(problems), parent=self)
            return
        if not charts:
            messagebox.showwarning("Logdef", t("logdef.choose_columns"), parent=self)
            return
        path = self.loaded_path
        if save_as or path is None:
            config_name = path.stem if path else (f"{self.logger}_{self.location}" if self.location else self.logger)
            selected = filedialog.asksaveasfilename(
                parent=self, title=t("logdef.save_title"), defaultextension=".logdef", initialdir=self.config_dir,
                initialfile=f"{config_name}.logdef", filetypes=[("Logdef", "*.logdef")])
            if not selected:
                return
            path = Path(selected)
        lines = [chart_to_line(chart, self.time_column) for chart in charts]
        try:
            backup_original(path)
            path.write_text(HEADER + "\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(t("dialog.save_failed"), str(exc), parent=self)
            return
        self.destroy()
        if self.on_saved:
            self.on_saved(path)
