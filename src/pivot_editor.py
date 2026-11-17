from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

import pandas as pd

from i18n import t
from pivots import AGGREGATIONS, PERIODS, calculate_pivots, load_pivots, save_pivots


class PivotEditor(tk.Toplevel):
    def __init__(self, parent: tk.Misc, data: pd.DataFrame, header_rows: int, path: Path, on_saved: Callable[[], None]):
        super().__init__(parent)
        self.title(t("pivot.editor"))
        self.geometry("800x480")
        self.data, self.header_rows, self.path, self.on_saved = data, header_rows, path, on_saved
        try:
            self.definitions = load_pivots(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(t("pivot.editor"), str(exc), parent=self)
            self.destroy()
            return
        self.current: int | None = None
        self.new_indices: set[int] = set()
        self.default_names: set[str] = set()
        left = ttk.Frame(self, padding=8)
        left.pack(side="left", fill="y")
        self.listbox = tk.Listbox(left, exportselection=False, width=28)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._select)
        ttk.Button(left, text=t("button.add"), command=self._add).pack(fill="x")
        ttk.Button(left, text=t("button.remove"), command=self._remove).pack(fill="x")
        ttk.Button(left, text=t("button.up"), command=lambda: self._move(-1)).pack(fill="x")
        ttk.Button(left, text=t("button.down"), command=lambda: self._move(1)).pack(fill="x")
        form = ttk.Frame(self, padding=12)
        form.pack(side="left", fill="both", expand=True)
        self.name = tk.StringVar()
        self.column = tk.StringVar()
        self.period = tk.StringVar()
        self.aggregation = tk.StringVar()
        self.period_labels = [t(f"pivot.period.{value}") for value in PERIODS]
        self.aggregation_labels = [t(f"pivot.aggregation.{value}") for value in AGGREGATIONS]
        self.name_entry = ttk.Entry(form, textvariable=self.name)
        rows = [
            (t("pivot.name"), self.name_entry),
            (t("pivot.source"), ttk.Combobox(form, textvariable=self.column, values=list(data.columns[1:]), state="readonly")),
            (t("pivot.interval"), ttk.Combobox(form, textvariable=self.period, values=self.period_labels, state="readonly")),
            (t("pivot.function"), ttk.Combobox(form, textvariable=self.aggregation, values=self.aggregation_labels, state="readonly")),
        ]
        self.field_widgets = [widget for _, widget in rows]
        for index, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=5)
            widget.grid(row=index, column=1, sticky="ew", padx=8, pady=5)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text=t("pivot.hint"), wraplength=480).grid(row=4, column=0, columnspan=2, sticky="w", pady=12)
        ttk.Button(form, text=t("formula.save_apply"), command=self._save).grid(row=5, column=1, sticky="e")
        for item in self.definitions:
            self.listbox.insert("end", item["name"])
        if self.definitions:
            self._load(0)
        else:
            self._add()

    def _commit(self) -> None:
        if self.current is None:
            return
        self.definitions[self.current] = {
            "name": self.name.get().strip(), "column": self.column.get(),
            "period": PERIODS[self.period_labels.index(self.period.get())],
            "aggregation": AGGREGATIONS[self.aggregation_labels.index(self.aggregation.get())],
        }
        self.listbox.delete(self.current)
        self.listbox.insert(self.current, self.name.get().strip() or t("pivot.unnamed", number=self.current + 1))

    def _load(self, index: int) -> None:
        self.current = index
        item = self.definitions[index]
        self.name.set(item["name"])
        self.column.set(item["column"])
        self.period.set(self.period_labels[PERIODS.index(item["period"])])
        self.aggregation.set(self.aggregation_labels[AGGREGATIONS.index(item["aggregation"])])
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)

    def _select(self, _event: object) -> None:
        selected = self.listbox.curselection()
        if selected and selected[0] != self.current:
            self._commit()
            self._load(selected[0])

    def _add(self) -> None:
        self._commit()
        name = t("pivot.unnamed", number=len(self.definitions) + 1)
        self.default_names.add(name)
        self.definitions.append({"name": name, "column": "", "period": "day", "aggregation": "mean"})
        self.new_indices.add(len(self.definitions) - 1)
        self.listbox.insert("end", t("pivot.unnamed", number=len(self.definitions)))
        self._load(len(self.definitions) - 1)
        self.after_idle(self._focus_name)

    def _focus_name(self) -> None:
        self.name_entry.focus_set()
        self.name_entry.select_range(0, "end")
        self.name_entry.icursor("end")

    def _remove(self) -> None:
        if self.current is None:
            return
        index = self.current
        del self.definitions[index]
        self.new_indices = {i if i < index else i - 1 for i in self.new_indices if i != index}
        self.listbox.delete(index)
        self.current = None
        if self.definitions:
            self._load(min(index, len(self.definitions) - 1))

    def _move(self, step: int) -> None:
        if self.current is None:
            return
        self._commit()
        current = self.current
        target = current + step
        if not 0 <= target < len(self.definitions):
            return
        self.definitions[current], self.definitions[target] = self.definitions[target], self.definitions[current]
        self.new_indices = {
            target if index == current else current if index == target else index
            for index in self.new_indices
        }
        self.listbox.delete(0, "end")
        for index, definition in enumerate(self.definitions):
            self.listbox.insert("end", definition["name"] or t("pivot.unnamed", number=index + 1))
        self._load(target)

    def _save(self) -> None:
        self._commit()
        definitions = []
        for index, definition in enumerate(self.definitions):
            if index in self.new_indices and definition == {
                "name": definition["name"], "column": "", "period": "day", "aggregation": "mean",
            } and (not definition["name"] or definition["name"] in self.default_names):
                continue
            missing = [
                (field_index, label)
                for field_index, (key, label) in enumerate((
                    ("name", t("pivot.name")), ("column", t("pivot.source")),
                    ("period", t("pivot.interval")), ("aggregation", t("pivot.function")),
                ))
                if not definition[key].strip()
            ]
            if missing:
                self._load(index)
                name = definition["name"] or t("pivot.unnamed", number=index + 1)
                messagebox.showerror(
                    t("pivot.editor"),
                    t("pivot.missing_fields", name=name, fields=", ".join(label for _, label in missing)),
                    parent=self,
                )
                self.field_widgets[missing[0][0]].focus_set()
                return
            definitions.append(definition)
        try:
            calculate_pivots(self.data, self.header_rows, definitions)
            save_pivots(self.path, definitions)
        except (OSError, ValueError) as exc:
            messagebox.showerror(t("pivot.editor"), str(exc), parent=self)
            return
        self.on_saved()
        self.destroy()
