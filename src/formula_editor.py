from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

import pandas as pd

from formulas import (
    FUNCDEF_HEADER, FUNCTION_HELP, FormulaError, apply_formulas, backup_original,
    evaluate_formula, formula_path, load_formulas, load_funcdefs, macro_help,
    parse_funcdefs, save_formulas,
)

FUNCDEF_NAME = "Functies.funcdef"


class FormulaEditor(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        data: pd.DataFrame,
        header_rows: int,
        logger: str,
        config_dir: Path,
        location: str | None = None,
        on_saved: Callable[[], None] | None = None,
        function_dir: Path | None = None,
    ):
        super().__init__(parent)
        self.title("Formules bewerken")
        self.geometry("1020x640")
        self.data = data
        self.header_rows = header_rows
        self.function_dir = Path(function_dir) if function_dir else None
        self.macros: dict = {}
        self._load_macros()
        self.path = formula_path(logger, location, config_dir)
        self.on_saved = on_saved
        self.headers = [str(column) for column in data.columns]
        try:
            self.formulas = load_formulas(logger, location, config_dir)
        except ValueError as exc:
            messagebox.showerror("Formules", str(exc), parent=self)
            self.formulas = []
        self.current: int | None = None
        self._build()
        if not self.formulas:
            self._add()
        else:
            for formula in self.formulas:
                self.formula_list.insert("end", formula["name"])
            self._load(0)

    def _build(self) -> None:
        left = ttk.Frame(self, padding=8)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Formules (volgorde = rekenvolgorde)").pack(anchor="w")
        self.formula_list = tk.Listbox(left, width=26, exportselection=False)
        self.formula_list.pack(fill="y", expand=True)
        self.formula_list.bind("<<ListboxSelect>>", self._on_select)
        for text, command in [("Toevoegen", self._add), ("Verwijderen", self._remove),
                              ("Omhoog", lambda: self._move(-1)), ("Omlaag", lambda: self._move(1))]:
            ttk.Button(left, text=text, command=command).pack(fill="x", pady=1)

        right = ttk.Frame(self, padding=8)
        right.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="Naam nieuwe kolom").grid(row=0, column=0, sticky="w", pady=2)
        self.name_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", padx=6, pady=2)
        ttk.Label(top, text="Formule").grid(row=1, column=0, sticky="nw", pady=2)
        self.expression = tk.Text(top, height=4, wrap="word", font=("Consolas", 10))
        self.expression.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        top.columnconfigure(1, weight=1)
        self.result_var = tk.StringVar()
        ttk.Label(right, textvariable=self.result_var, wraplength=700).pack(anchor="w", pady=(6, 0))

        pick = ttk.Frame(right)
        pick.pack(fill="both", expand=True, pady=(10, 0))
        columns_frame = ttk.Frame(pick)
        columns_frame.pack(side="left", fill="both", expand=True)
        ttk.Label(columns_frame, text="Kolommen (dubbelklik = invoegen)").pack(anchor="w")
        self.columns_box = tk.Listbox(columns_frame, exportselection=False)
        self.columns_box.pack(fill="both", expand=True)
        self.columns_box.bind("<Double-1>", self._insert_column)
        ttk.Button(columns_frame, text="Kolom invoegen", command=lambda: self._insert_column(None)).pack(fill="x")
        functions_frame = ttk.Frame(pick)
        functions_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))
        ttk.Label(functions_frame, text="Functies (dubbelklik = invoegen)").pack(anchor="w")
        self.functions_box = tk.Listbox(functions_frame, exportselection=False)
        self.functions_box.pack(fill="both", expand=True)
        self.functions_box.bind("<Double-1>", self._insert_function)
        ttk.Button(functions_frame, text="Functie invoegen", command=lambda: self._insert_function(None)).pack(fill="x")
        self.function_names: list[str] = []
        self._fill_functions()

        bottom = ttk.Frame(right)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Test formule", command=self._test).pack(side="left")
        if self.function_dir:
            ttk.Button(bottom, text="Eigen functies bewerken...", command=self._edit_funcdef).pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text="Opslaan en toepassen", command=self._save).pack(side="right")

    def _refresh_columns(self) -> None:
        earlier = [f["name"] for f in self.formulas[: self.current or 0] if f["name"]]
        self.columns_box.delete(0, "end")
        for header in self.headers + earlier:
            self.columns_box.insert("end", header)

    def _insert_text(self, text: str) -> None:
        self.expression.insert("insert", text)
        self.expression.focus_set()

    def _insert_column(self, _event: object) -> None:
        selection = self.columns_box.curselection()
        if selection:
            self._insert_text(f"[{self.columns_box.get(selection[0])}]")

    def _insert_function(self, _event: object) -> None:
        selection = self.functions_box.curselection()
        if selection:
            name = self.function_names[selection[0]]
            macro = self.macros.get(name.upper())
            self._insert_text(name if macro and macro["params"] is None else name + "(")

    def _load_macros(self) -> None:
        try:
            self.macros = load_funcdefs(self.function_dir) if self.function_dir else {}
        except ValueError as exc:
            self.macros = {}
            messagebox.showerror("Eigen functies", str(exc), parent=self)

    def _fill_functions(self) -> None:
        descriptions = {**FUNCTION_HELP, **macro_help(self.macros)}
        self.function_names = list(descriptions)
        self.functions_box.delete(0, "end")
        for description in descriptions.values():
            self.functions_box.insert("end", description)

    def _edit_funcdef(self) -> None:
        assert self.function_dir is not None
        path = self.function_dir / FUNCDEF_NAME
        window = tk.Toplevel(self)
        window.title(f"Eigen functies - {path.name}")
        window.geometry("820x520")
        text = tk.Text(window, wrap="none", font=("Consolas", 10), undo=True)
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", path.read_text(encoding="utf-8") if path.is_file() else FUNCDEF_HEADER)

        def save() -> None:
            content = text.get("1.0", "end").rstrip() + "\n"
            try:
                parse_funcdefs(content, path.name)
            except ValueError as exc:
                messagebox.showerror("Eigen functies", str(exc), parent=window)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            backup_original(path)
            path.write_text(content, encoding="utf-8")
            self._load_macros()
            self._fill_functions()
            if self.on_saved:
                self.on_saved()
            window.destroy()

        ttk.Button(window, text="Opslaan", command=save).pack(side="right", padx=8, pady=(0, 8))

    def _commit(self) -> None:
        if self.current is None:
            return
        formula = self.formulas[self.current]
        formula["name"] = self.name_var.get().strip() or f"Berekening {self.current + 1}"
        formula["expression"] = self.expression.get("1.0", "end").strip()
        self.formula_list.delete(self.current)
        self.formula_list.insert(self.current, formula["name"])

    def _load(self, index: int) -> None:
        self.current = index
        formula = self.formulas[index]
        self.name_var.set(formula["name"])
        self.expression.delete("1.0", "end")
        self.expression.insert("1.0", formula["expression"])
        self.result_var.set("")
        self._refresh_columns()
        self.formula_list.selection_clear(0, "end")
        self.formula_list.selection_set(index)

    def _on_select(self, _event: object) -> None:
        selection = self.formula_list.curselection()
        if selection and selection[0] != self.current:
            self._commit()
            self._load(selection[0])

    def _add(self) -> None:
        self._commit()
        self.formulas.append({"name": f"Berekening {len(self.formulas) + 1}", "expression": ""})
        self.formula_list.insert("end", self.formulas[-1]["name"])
        self._load(len(self.formulas) - 1)

    def _remove(self) -> None:
        if self.current is None:
            return
        del self.formulas[self.current]
        self.formula_list.delete(self.current)
        self.current = None
        if self.formulas:
            self._load(min(self.formula_list.size() - 1, len(self.formulas) - 1))
        else:
            self._add()

    def _move(self, step: int) -> None:
        if self.current is None:
            return
        self._commit()
        target = self.current + step
        if not 0 <= target < len(self.formulas):
            return
        self.formulas[self.current], self.formulas[target] = self.formulas[target], self.formulas[self.current]
        self.formula_list.delete(0, "end")
        for formula in self.formulas:
            self.formula_list.insert("end", formula["name"])
        self._load(target)

    def _test(self) -> None:
        self._commit()
        if self.current is None:
            return
        base, _names, _errors = apply_formulas(self.data, self.formulas[: self.current], self.header_rows, self.macros)
        try:
            values = evaluate_formula(self.formulas[self.current]["expression"], base, self.header_rows, self.macros)
        except FormulaError as exc:
            self.result_var.set(f"Fout: {exc}")
            return
        valid = values.dropna()
        if valid.empty:
            self.result_var.set("Formule geldig, maar er zijn geen waarden berekend.")
        else:
            self.result_var.set(
                f"OK: {len(valid):,} waarden | min {valid.min():.4g} | max {valid.max():.4g} | gemiddelde {valid.mean():.4g}"
            )

    def _save(self) -> None:
        self._commit()
        formulas = [f for f in self.formulas if f["expression"]]
        names = [f["name"].upper() for f in formulas]
        if len(set(names)) != len(names):
            messagebox.showwarning("Formules", "Elke formule moet een unieke naam hebben.", parent=self)
            return
        try:
            save_formulas(self.path, formulas)
        except OSError as exc:
            messagebox.showerror("Formules", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.destroy()
