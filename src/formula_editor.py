from __future__ import annotations

from pathlib import Path
import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

import pandas as pd

from formulas import (
    FormulaError, add_funcdef_groups, apply_formulas, backup_original,
    evaluate_formula, formula_path, function_help, funcdef_header, load_formulas, load_funcdefs, macro_help,
    parse_funcdefs, save_formulas,
)
from function_catalog import (
    CATALOG_NAME, FunctionEntry, FunctionGroup, include_unlisted, load_catalog, parse_catalog, save_catalog,
    serialize_catalog, sync_catalog,
)
from i18n import t

FUNCDEF_NAME = "Functies.funcdef"


def _add_text_search_bar(
    parent: tk.Misc, text: tk.Text,
) -> tuple[ttk.Entry, ttk.Button, ttk.Button]:
    bar = ttk.Frame(parent, padding=(8, 4))
    bar.pack(fill="x")
    ttk.Label(bar, text=t("formula.find")).pack(side="left")
    query = tk.StringVar(master=parent)
    entry = ttk.Entry(bar, textvariable=query, width=28)
    entry.pack(side="left", fill="x", expand=True, padx=6)
    previous_button = ttk.Button(bar, text=t("formula.find_previous"))
    previous_button.pack(side="left", padx=(0, 4))
    next_button = ttk.Button(bar, text=t("formula.find_next"))
    next_button.pack(side="left")
    status = tk.StringVar(master=parent)
    ttk.Label(bar, textvariable=status, width=12, anchor="e").pack(side="left", padx=(6, 0))

    text.tag_configure("find_match", background="#FFF2A8", foreground="#000000")
    text.tag_configure("find_current", background="#FFB347", foreground="#000000")
    matches: list[tuple[str, str]] = []
    current = -1

    def refresh_matches(*_args: object) -> None:
        nonlocal matches, current
        text.edit_modified(False)
        text.tag_remove("find_match", "1.0", "end")
        text.tag_remove("find_current", "1.0", "end")
        matches = []
        current = -1
        value = query.get()
        if not value:
            status.set("")
            return
        position = "1.0"
        while True:
            found = text.search(value, position, stopindex="end-1c", nocase=True)
            if not found:
                break
            end = text.index(f"{found}+{len(value)}c")
            matches.append((found, end))
            text.tag_add("find_match", found, end)
            position = end
        status.set(t("formula.find_count", current=0, total=len(matches))
                   if matches else t("formula.find_none"))

    def move_match(step: int) -> str:
        nonlocal current
        if not matches:
            return "break"
        current = (current + step) % len(matches) if current >= 0 else (0 if step > 0 else len(matches) - 1)
        start, end = matches[current]
        text.tag_remove("find_current", "1.0", "end")
        text.tag_add("find_current", start, end)
        text.mark_set("insert", start)
        text.see(start)
        status.set(t("formula.find_count", current=current + 1, total=len(matches)))
        return "break"

    previous_button.configure(command=lambda: move_match(-1))
    next_button.configure(command=lambda: move_match(1))
    entry.bind("<Return>", lambda _event: move_match(1))
    entry.bind("<Shift-Return>", lambda _event: move_match(-1))
    query.trace_add("write", refresh_matches)
    text.bind("<<Modified>>", lambda _event: refresh_matches(), add="+")

    def focus_find(_event: tk.Event | None = None) -> str:
        entry.focus_set()
        entry.selection_range(0, "end")
        return "break"

    parent.winfo_toplevel().bind("<Control-f>", focus_find, add="+")
    text.bind("<Control-f>", focus_find, add="+")
    entry.bind("<Control-f>", focus_find, add="+")
    return entry, previous_button, next_button


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
        self.title(t("formula.title"))
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
            messagebox.showerror(t("dialog.formulas"), str(exc), parent=self)
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
        ttk.Label(left, text=t("formula.list_title")).pack(anchor="w")
        self.formula_list = tk.Listbox(left, width=26, exportselection=False)
        self.formula_list.pack(fill="y", expand=True)
        self.formula_list.bind("<<ListboxSelect>>", self._on_select)
        for text, command in [(t("button.add"), self._add), (t("button.remove"), self._remove),
                              (t("button.up"), lambda: self._move(-1)), (t("button.down"), lambda: self._move(1))]:
            ttk.Button(left, text=text, command=command).pack(fill="x", pady=1)

        right = ttk.Frame(self, padding=8)
        right.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text=t("formula.new_column_name")).grid(row=0, column=0, sticky="w", pady=2)
        self.name_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", padx=6, pady=2)
        ttk.Label(top, text=t("formula.formula")).grid(row=1, column=0, sticky="nw", pady=2)
        self.expression = tk.Text(top, height=4, wrap="word", font=("Consolas", 10))
        self.expression.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        top.columnconfigure(1, weight=1)
        self.result_var = tk.StringVar()
        ttk.Label(right, textvariable=self.result_var, wraplength=700).pack(anchor="w", pady=(6, 0))

        pick = ttk.Frame(right)
        pick.pack(fill="both", expand=True, pady=(10, 0))
        columns_frame = ttk.Frame(pick)
        columns_frame.pack(side="left", fill="both", expand=True)
        ttk.Label(columns_frame, text=t("formula.columns_title")).pack(anchor="w")
        self.columns_box = tk.Listbox(columns_frame, exportselection=False)
        self.columns_box.pack(fill="both", expand=True)
        self.columns_box.bind("<Double-1>", self._insert_column)
        functions_frame = ttk.Frame(pick)
        functions_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))
        ttk.Label(functions_frame, text=t("formula.functions_title")).pack(anchor="w")
        self.functions_box = tk.Listbox(functions_frame, exportselection=False)
        self.functions_box.pack(fill="both", expand=True)
        self.functions_box.bind("<Double-1>", self._insert_function)
        self.function_names: list[str | None] = []
        self.catalog: list[FunctionGroup] | None = None
        self._fill_functions()
        if self.function_dir:
            ttk.Button(functions_frame, text=t("formula.select_functions"),
                       command=self._select_functions).pack(anchor="w", pady=(4, 0))
            ttk.Button(functions_frame, text=t("formula.edit_catalog"),
                       command=self._edit_catalog).pack(anchor="w", pady=(4, 0))

        bottom = ttk.Frame(right)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text=t("formula.test"), command=self._test).pack(side="left")
        if self.function_dir:
            ttk.Button(bottom, text=t("formula.edit_functions"), command=self._edit_funcdef).pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text=t("formula.save_apply"), command=self._save).pack(side="right")

    def _refresh_columns(self) -> None:
        calc = [f["name"] for f in self.formulas if f["name"] and f["name"] not in self.headers]
        self.columns_box.delete(0, "end")
        for header in self.headers + calc:
            self.columns_box.insert("end", header)

    def _insert_text(self, text: str, cursor_before_end: int = 0) -> None:
        self.expression.insert("insert", text)
        if cursor_before_end:
            self.expression.mark_set("insert", f"insert-{cursor_before_end}c")
        self.expression.focus_set()

    def _insert_column(self, _event: object) -> None:
        selection = self.columns_box.curselection()
        if selection:
            self._insert_text(f"[{self.columns_box.get(selection[0])}],")

    def _insert_function(self, _event: object) -> None:
        selection = self.functions_box.curselection()
        if selection:
            name = self.function_names[selection[0]]
            if name is None:
                return
            macro = self.macros.get(name.upper())
            if macro and macro["params"] is None:
                self._insert_text(name)
            else:
                self._insert_text(name + "()", cursor_before_end=1)

    def _load_macros(self) -> None:
        try:
            self.macros = load_funcdefs(self.function_dir) if self.function_dir else {}
            self.macros_loaded = True
        except (OSError, ValueError) as exc:
            self.macros = {}
            self.macros_loaded = False
            messagebox.showerror(t("formula.own_functions"), str(exc), parent=self)

    def _fill_functions(self) -> None:
        descriptions = {**function_help(), **macro_help(self.macros)}
        by_name = {name.upper(): (name, description) for name, description in descriptions.items()}
        try:
            self.catalog = load_catalog(self.function_dir / CATALOG_NAME) if self.function_dir else []
            if self.function_dir and self.macros_loaded:
                before = [(group.name, group.color, [(entry.name, entry.visible) for entry in group.functions])
                          for group in self.catalog]
                sync_catalog(self.catalog, list(function_help()), self.macros)
                after = [(group.name, group.color, [(entry.name, entry.visible) for entry in group.functions])
                         for group in self.catalog]
                if before != after:
                    save_catalog(self.function_dir / CATALOG_NAME, self.catalog)
        except (OSError, ValueError) as exc:
            messagebox.showerror(t("formula.select_functions"), str(exc), parent=self)
            self.catalog = None
        groups = self.catalog if self.catalog is not None else []
        include_unlisted(groups, list(descriptions))
        self.function_names = []
        self.functions_box.delete(0, "end")
        for group in groups:
            entries = [entry for entry in group.functions if entry.visible and entry.name.upper() in by_name]
            if not entries:
                continue
            self.functions_box.insert("end", f"--- {group.name} ---")
            self.function_names.append(None)
            self.functions_box.itemconfig("end", foreground=group.color)
            for entry in entries:
                name, description = by_name[entry.name.upper()]
                self.functions_box.insert("end", description)
                self.function_names.append(name)
                self.functions_box.itemconfig("end", foreground=group.color)

    def _select_functions(self) -> None:
        if self.catalog is None or self.function_dir is None:
            return
        descriptions = {name.upper(): description for name, description in
                        {**function_help(), **macro_help(self.macros)}.items()}
        window = tk.Toplevel(self)
        window.title(t("formula.select_functions"))
        window.geometry("700x500")
        frame = ttk.Frame(window, padding=8)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=t("formula.select_functions_hint")).pack(anchor="w")
        box = tk.Listbox(frame, selectmode="multiple", exportselection=False)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        box.pack(fill="both", expand=True)
        entries: list[FunctionEntry] = []
        for group in self.catalog:
            for entry in group.functions:
                description = descriptions.get(entry.name.upper())
                if description is None:
                    continue
                index = len(entries)
                entries.append(entry)
                box.insert("end", f"{group.name}: {description}")
                box.itemconfig(index, foreground=group.color)
                if entry.visible:
                    box.selection_set(index)

        def save() -> None:
            selected = set(box.curselection())
            previous = [entry.visible for entry in entries]
            for index, entry in enumerate(entries):
                entry.visible = index in selected
            try:
                save_catalog(self.function_dir / CATALOG_NAME, self.catalog)
            except OSError as exc:
                for entry, visible in zip(entries, previous):
                    entry.visible = visible
                messagebox.showerror(t("formula.select_functions"), str(exc), parent=window)
                return
            window.destroy()
            self._fill_functions()

        ttk.Button(window, text=t("button.save"), command=save).pack(side="right", padx=8, pady=(0, 8))

    def _edit_catalog(self) -> None:
        assert self.function_dir is not None
        path = self.function_dir / CATALOG_NAME
        try:
            content = path.read_text(encoding="utf-8") if path.is_file() else serialize_catalog(self.catalog or [])
        except OSError as exc:
            messagebox.showerror(t("formula.catalog_editor"), str(exc), parent=self)
            return

        window = tk.Toplevel(self)
        window.title(t("formula.catalog_editor_title", name=path.name))
        window.geometry("760x520")
        ttk.Label(window, text=t("formula.catalog_editor_hint"), wraplength=730).pack(
            anchor="w", padx=8, pady=(8, 4)
        )
        text = tk.Text(window, wrap="none", font=("Consolas", 10), undo=True)
        _add_text_search_bar(window, text)
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(0, 8), pady=8)
        text.pack(side="left", fill="both", expand=True, padx=8, pady=(0, 8))
        text.insert("1.0", content)
        text.edit_modified(False)

        def save() -> None:
            try:
                groups = parse_catalog(text.get("1.0", "end-1c"), path)
                save_catalog(path, groups)
            except (OSError, ValueError) as exc:
                messagebox.showerror(t("formula.catalog_editor"), str(exc), parent=window)
                return
            self._fill_functions()
            window.destroy()

        ttk.Button(window, text=t("button.save"), command=save).pack(side="right", padx=8, pady=(0, 8))

    def _edit_funcdef(self) -> None:
        assert self.function_dir is not None
        path = self.function_dir / FUNCDEF_NAME
        window = tk.Toplevel(self)
        window.title(t("formula.own_functions_title", name=path.name))
        window.geometry("820x520")
        line_numbers = tk.Text(
            window, width=4, wrap="none", font=("Consolas", 10), state="disabled",
            takefocus=False, foreground="#666666", background="#f0f0f0",
            relief="flat", borderwidth=0, padx=4,
        )
        text = tk.Text(window, wrap="none", font=("Consolas", 10), undo=True)
        _add_text_search_bar(window, text)
        line_numbers.pack(side="left", fill="y", padx=(8, 0), pady=8)
        text.pack(side="left", fill="both", expand=True, padx=(0, 0), pady=8)

        def scroll_editor(*args: str) -> None:
            text.yview(*args)
            line_numbers.yview(*args)

        scrollbar = ttk.Scrollbar(window, orient="vertical", command=scroll_editor)
        scrollbar.pack(side="right", fill="y", padx=(0, 8), pady=8)

        def sync_scroll(first: str, last: str) -> None:
            scrollbar.set(first, last)
            line_numbers.yview_moveto(first)

        text.configure(yscrollcommand=sync_scroll)

        def update_line_numbers(_event: tk.Event | None = None) -> None:
            text.edit_modified(False)
            line_count = max(1, int(text.index("end-1c").split(".")[0]))
            width = max(4, len(str(line_count)) + 1)
            line_numbers.configure(state="normal", width=width)
            line_numbers.delete("1.0", "end")
            line_numbers.insert("1.0", "\n".join(str(number) for number in range(1, line_count + 1)))
            line_numbers.configure(state="disabled")

        text.bind("<<Modified>>", update_line_numbers)
        text.insert("1.0", path.read_text(encoding="utf-8") if path.is_file() else funcdef_header())
        update_line_numbers()

        def save() -> None:
            content = text.get("1.0", "end").rstrip() + "\n"
            try:
                parse_funcdefs(content, path.name)
                groups = load_catalog(self.function_dir / CATALOG_NAME)
                group_by_name = {entry.name.upper(): group.name for group in groups for entry in group.functions}
                content = add_funcdef_groups(content, group_by_name, t("formula.catalog_other"))
                for other in self.function_dir.glob("*.funcdef"):
                    if other != path:
                        parse_funcdefs(other.read_text(encoding="utf-8"), other.name)
                path.parent.mkdir(parents=True, exist_ok=True)
                backup_original(path)
                path.write_text(content, encoding="utf-8")
            except (OSError, ValueError) as exc:
                messagebox.showerror(t("formula.own_functions"), str(exc), parent=window)
                return
            self._load_macros()
            self._fill_functions()
            if self.on_saved:
                self.on_saved()
            window.destroy()

        ttk.Button(window, text=t("button.save"), command=save).pack(side="right", padx=8, pady=(0, 8))

    def _commit(self) -> None:
        if self.current is None:
            return
        formula = self.formulas[self.current]
        formula["name"] = self.name_var.get().strip() or t("formula.default_name", number=self.current + 1)
        expression = self.expression.get("1.0", "end").strip()
        expression = re.sub(r",(?=\s*\))", "", expression)
        formula["expression"] = expression[:-1].rstrip() if expression.endswith(",") else expression
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
        self.formulas.append({"name": t("formula.default_name", number=len(self.formulas) + 1), "expression": ""})
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
            self.result_var.set(t("formula.error", error=exc))
            return
        valid = values.dropna()
        if valid.empty:
            self.result_var.set(t("formula.valid_no_values"))
        else:
            self.result_var.set(t(
                "formula.ok", count=f"{len(valid):,}", min=f"{valid.min():.4g}",
                max=f"{valid.max():.4g}", mean=f"{valid.mean():.4g}",
            ))

    def _save(self) -> None:
        self._commit()
        formulas = [f for f in self.formulas if f["expression"]]
        names = [f["name"].upper() for f in formulas]
        if len(set(names)) != len(names):
            messagebox.showwarning(t("dialog.formulas"), t("formula.unique_names"), parent=self)
            return
        try:
            save_formulas(self.path, formulas)
        except OSError as exc:
            messagebox.showerror(t("dialog.formulas"), str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.destroy()
