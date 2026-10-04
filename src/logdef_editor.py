from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

CHART_TYPES = ["line", "bar", "scatter", "windroos"]
COLORS = ["", "red", "blue", "cyan", "yellow", "green", "orange", "purple", "gray", "black"]
HEADER = (
    "# name;type;columns;minimum;maximum;number format;tab color\n"
    "# type=windroos uses one direction-column header in columns; omit the line for loggers without a windrose.\n"
    "# tab color: hex code or a readable color name\n"
)


def new_chart(index: int) -> dict:
    return {"name": f"Grafiek {index}", "type": "line", "columns": [], "minimum": "", "maximum": "", "format": "0.0", "color": ""}


def chart_to_line(chart: dict, time_column: str) -> str:
    columns = list(chart["columns"])
    # Lijn- en staafgrafieken gebruiken de tijdkolom als x-as.
    if chart["type"] in {"line", "bar"} and not any(c.split("@")[0] == time_column for c in columns):
        columns.insert(0, time_column)
    fields = [chart["name"], chart["type"], ",".join(columns), chart["minimum"], chart["maximum"], chart["format"], chart["color"]]
    return ";".join(str(f).replace(";", ",").strip() for f in fields)


class LogdefEditor(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        headers: list[str],
        logger: str,
        config_dir: Path,
        location: str | None = None,
    ):
        super().__init__(parent)
        self.title("Nieuwe .logdef maken")
        self.geometry("980x600")
        self.headers = headers
        self.time_column = headers[0] if headers else "TIMESTAMP"
        self.logger = logger
        self.location = location
        self.config_dir = Path(config_dir)
        self.charts: list[dict] = []
        self.current: int | None = None
        self._build()
        self._add_chart()

    def _build(self) -> None:
        left = ttk.Frame(self, padding=8)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Grafieken").pack(anchor="w")
        self.chart_list = tk.Listbox(left, width=24, exportselection=False)
        self.chart_list.pack(fill="y", expand=True)
        self.chart_list.bind("<<ListboxSelect>>", self._on_select)
        for text, command in [("Toevoegen", self._add_chart), ("Verwijderen", self._remove_chart),
                              ("Omhoog", lambda: self._move(-1)), ("Omlaag", lambda: self._move(1))]:
            ttk.Button(left, text=text, command=command).pack(fill="x", pady=1)

        right = ttk.Frame(self, padding=8)
        right.pack(side="left", fill="both", expand=True)
        form = ttk.Frame(right)
        form.pack(fill="x")
        self.name_var = tk.StringVar()
        self.type_var = tk.StringVar(value="line")
        self.min_var = tk.StringVar()
        self.max_var = tk.StringVar()
        self.format_var = tk.StringVar(value="0.0")
        self.color_var = tk.StringVar()
        rows = [("Naam", ttk.Entry(form, textvariable=self.name_var)),
                ("Type", ttk.Combobox(form, textvariable=self.type_var, values=CHART_TYPES, state="readonly")),
                ("Minimum", ttk.Entry(form, textvariable=self.min_var)),
                ("Maximum", ttk.Entry(form, textvariable=self.max_var)),
                ("Getalformaat", ttk.Entry(form, textvariable=self.format_var)),
                ("Tabkleur", ttk.Combobox(form, textvariable=self.color_var, values=COLORS))]
        for row, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
            widget.grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        form.columnconfigure(1, weight=1)
        rows[1][1].bind("<<ComboboxSelected>>", lambda _e: self._update_hint())

        pick = ttk.Frame(right)
        pick.pack(fill="both", expand=True, pady=(10, 0))
        avail = ttk.Frame(pick)
        avail.pack(side="left", fill="both", expand=True)
        ttk.Label(avail, text="Kolommen in .dat").pack(anchor="w")
        self.available = tk.Listbox(avail, selectmode="extended", exportselection=False)
        self.available.pack(fill="both", expand=True)
        for header in self.headers:
            self.available.insert("end", header)

        buttons = ttk.Frame(pick, padding=6)
        buttons.pack(side="left")
        ttk.Button(buttons, text="Primair >", command=lambda: self._assign(False)).pack(fill="x", pady=2)
        ttk.Button(buttons, text="Secundair >", command=lambda: self._assign(True)).pack(fill="x", pady=2)
        ttk.Button(buttons, text="< Verwijder", command=self._unassign).pack(fill="x", pady=2)

        chosen = ttk.Frame(pick)
        chosen.pack(side="left", fill="both", expand=True)
        self.hint = tk.StringVar()
        ttk.Label(chosen, textvariable=self.hint).pack(anchor="w")
        self.selected = tk.Listbox(chosen, selectmode="extended", exportselection=False)
        self.selected.pack(fill="both", expand=True)

        bottom = ttk.Frame(right)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Laad .logdef...", command=self._open).pack(side="left")
        ttk.Button(bottom, text="Opslaan als .logdef...", command=self._save).pack(side="right")

    def _update_hint(self) -> None:
        kind = self.type_var.get()
        self.hint.set({
            "line": "Gekozen kolommen (tijd wordt automatisch x-as)",
            "bar": "Gekozen kolommen (tijd wordt automatisch x-as)",
            "scatter": "Gekozen kolommen (eerste = X, tweede = Y)",
            "windroos": "Gekozen kolom (1 richtingskolom)",
        }[kind])

    def _commit(self) -> None:
        if self.current is None:
            return
        chart = self.charts[self.current]
        chart.update(name=self.name_var.get().strip() or f"Grafiek {self.current + 1}", type=self.type_var.get(),
                     minimum=self.min_var.get().strip(), maximum=self.max_var.get().strip(),
                     format=self.format_var.get().strip(), color=self.color_var.get().strip(),
                     columns=list(self.selected.get(0, "end")))
        self.chart_list.delete(self.current)
        self.chart_list.insert(self.current, chart["name"])

    def _load(self, index: int) -> None:
        self.current = index
        chart = self.charts[index]
        self.name_var.set(chart["name"])
        self.type_var.set(chart["type"])
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
        existing = {c.split("@")[0] for c in self.selected.get(0, "end")}
        single = self.type_var.get() == "windroos"
        for index in self.available.curselection():
            header = self.headers[index]
            if header in existing:
                continue
            if single:
                self.selected.delete(0, "end")
            self.selected.insert("end", f"{header}@secondary" if secondary and self.type_var.get() == "line" else header)
            existing.add(header)

    def _unassign(self) -> None:
        for index in reversed(self.selected.curselection()):
            self.selected.delete(index)

    def _open(self) -> None:
        config_name = f"{self.logger}_{self.location}" if self.location else self.logger
        path = filedialog.askopenfilename(
            parent=self, title="Open .logdef", initialdir=self.config_dir,
            initialfile=f"{config_name}.logdef", filetypes=[("Logdef", "*.logdef")])
        if not path:
            return
        charts = []
        for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            fields = [f.strip() for f in line.split(";")]
            if len(fields) != 7:
                messagebox.showerror("Logdef", f"Ongeldige regel {number} in {Path(path).name}", parent=self)
                return
            name, kind, columns, minimum, maximum, fmt, color = fields
            cols = [c.strip() for c in columns.split(",") if c.strip()]
            # De tijdkolom wordt bij opslaan weer automatisch toegevoegd.
            if kind.lower() in {"line", "bar"} and cols and cols[0].upper() == self.time_column.upper():
                cols = cols[1:]
            charts.append({"name": name, "type": kind.lower(), "columns": cols, "minimum": minimum,
                           "maximum": maximum, "format": fmt, "color": color})
        if not charts:
            messagebox.showwarning("Logdef", "Geen grafieken gevonden in dit bestand.", parent=self)
            return
        self.charts = charts
        self.current = None
        self.chart_list.delete(0, "end")
        for chart in charts:
            self.chart_list.insert("end", chart["name"])
        self._load(0)

    def _save(self) -> None:
        self._commit()
        problems = []
        charts = []
        for chart in self.charts:
            if not chart["columns"]:
                continue
            needed = 2 if chart["type"] == "scatter" else 1
            if len(chart["columns"]) < needed:
                problems.append(f"'{chart['name']}': kies minimaal {needed} kolom(men).")
            charts.append(chart)
        if problems:
            messagebox.showwarning("Logdef", "\n".join(problems), parent=self)
            return
        if not charts:
            messagebox.showwarning("Logdef", "Kies kolommen voor minstens één grafiek.", parent=self)
            return
        config_name = f"{self.logger}_{self.location}" if self.location else self.logger
        path = filedialog.asksaveasfilename(
            parent=self, title="Opslaan als", defaultextension=".logdef", initialdir=self.config_dir,
            initialfile=f"{config_name}.logdef", filetypes=[("Logdef", "*.logdef")])
        if not path:
            return
        lines = [chart_to_line(chart, self.time_column) for chart in charts]
        Path(path).write_text(HEADER + "\n".join(lines) + "\n", encoding="utf-8")
        messagebox.showinfo("Logdef", f"Opgeslagen: {path}", parent=self)
