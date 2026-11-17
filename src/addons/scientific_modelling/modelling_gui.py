from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from . import data_analysis as da
from . import model_store, regression, time_series, validation
from .model_core import ModelSpec
from .physical_models import PHYSICAL_MODELS, PhysicalModel, run_physical_model
from .simulation import sensitivity, simulate
from i18n import t

MAX_SCATTER_POINTS = 5000


def _number(text: str, label: str = "") -> float:
    try:
        return float(str(text).strip().replace(",", "."))
    except ValueError as exc:
        raise ValueError(t("addon.modelling.invalid_number_for", label=label) if label else t("addon.modelling.invalid_number")) from exc


def _table(frame: pd.DataFrame | pd.Series) -> str:
    return frame.to_string(float_format=lambda v: f"{v:.4g}")


class PlotPanel(ttk.Frame):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.figure = Figure(figsize=(7, 4), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        toolbar = NavigationToolbar2Tk(self.canvas, self, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="bottom", fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def reset(self) -> Figure:
        self.figure.clear()
        return self.figure

    def draw(self) -> None:
        try:
            self.figure.tight_layout()
        except ValueError:
            pass
        self.canvas.draw_idle()


class ModellingWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, frame: pd.DataFrame, source_name: str, model_dir: Path | None = None):
        super().__init__(parent)
        self.model_dir = model_dir
        self.frame = frame
        self.names: dict[str, str] = frame.attrs.get("names", {})
        self.keys = list(frame.columns)
        self.labels = [self._label(k) for k in self.keys]
        self.model: ModelSpec | None = None
        self.title(t("addon.modelling.analysis_and_modelling") + f" | {source_name}")
        self.geometry("1280x840")
        self.minsize(1000, 650)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.status = tk.StringVar(value=t("addon.modelling.no_active_model"))
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", side="bottom")
        ttk.Button(bottom, text=t("addon.modelling.load_model_2"), command=lambda: self._guarded(self._load_model)).pack(side="right", padx=8, pady=4)
        ttk.Button(bottom, text=t("addon.modelling.save_model_2"), command=lambda: self._guarded(self._save_model)).pack(side="right", pady=4)
        ttk.Label(bottom, textvariable=self.status, padding=(10, 4)).pack(side="left", fill="x")
        self._build_analysis_tab()
        self._build_builder_tab()
        self._build_physical_tab()
        self._build_evaluation_tab()
        self._build_simulation_tab()

    def _label(self, key: str) -> str:
        original = self.names.get(key, key)
        return key if original == key else f"{key}  ({original})"

    @staticmethod
    def _key(label: str) -> str:
        return label.split("  (")[0].strip()

    def _guarded(self, action: Callable[[], None]) -> None:
        try:
            action()
        except (ValueError, KeyError, np.linalg.LinAlgError) as exc:
            messagebox.showerror(t("addon.modelling.analysis_and_modelling"), str(exc), parent=self)

    def _listbox(self, parent: tk.Misc, height: int = 14, mode: str = "extended") -> tk.Listbox:
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True)
        box = tk.Listbox(holder, selectmode=mode, exportselection=False, height=height, width=34)
        bar = ttk.Scrollbar(holder, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        box.pack(side="left", fill="both", expand=True)
        for label in self.labels:
            box.insert("end", label)
        return box

    @staticmethod
    def _output(parent: tk.Misc, height: int = 9) -> tk.Text:
        text = tk.Text(parent, height=height, wrap="none", font=("Consolas", 9), state="disabled")
        text.pack(fill="x")
        return text

    @staticmethod
    def _write(widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _selected(self, box: tk.Listbox, minimum: int = 1) -> list[str]:
        keys = [self._key(box.get(i)) for i in box.curselection()]
        if len(keys) < minimum:
            raise ValueError(t("addon.modelling.select_at_least_variable_s", minimum=minimum))
        return keys

    def _combo(self, parent: tk.Misc, values: list[str] | None = None, width: int = 32) -> ttk.Combobox:
        box = ttk.Combobox(parent, values=self.labels if values is None else values, state="readonly", width=width)
        box.pack(fill="x", pady=(0, 6))
        return box

    def _tab(self, title: str) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text=title)
        return tab

    def _side_and_main(self, tab: ttk.Frame, side_width: int = 290) -> tuple[ttk.Frame, ttk.Frame]:
        side = ttk.Frame(tab, width=side_width)
        side.pack(side="left", fill="y", padx=(0, 10))
        main = ttk.Frame(tab)
        main.pack(side="left", fill="both", expand=True)
        return side, main

    def _entry(self, parent: tk.Misc, label: str, value: str = "", width: int = 14) -> ttk.Entry:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=label).pack(side="left")
        entry = ttk.Entry(row, width=width)
        entry.insert(0, value)
        entry.pack(side="right")
        return entry

    def _button(self, parent: tk.Misc, text: str, action: Callable[[], None]) -> None:
        ttk.Button(parent, text=text, command=lambda: self._guarded(action)).pack(fill="x", pady=2)

    def _subset(self, start: str, end: str) -> pd.DataFrame:
        return da.select_range(self.frame, start, end)

    # ---------- Tab 1: Data Analysis ----------
    def _build_analysis_tab(self) -> None:
        side, main = self._side_and_main(self._tab(t("addon.modelling.data_analysis")))
        ttk.Label(side, text=t("addon.modelling.measured_variables_multiple")).pack(anchor="w")
        self.an_list = self._listbox(side, 16)
        first, last = self.frame.index[0].strftime("%Y-%m-%d"), self.frame.index[-1].strftime("%Y-%m-%d")
        self.an_start = self._entry(side, t("addon.modelling.from_yyyy_mm_dd"), first)
        self.an_end = self._entry(side, t("addon.modelling.to"), last)
        self.an_method = ttk.Combobox(side, values=["pearson", "spearman", "kendall"], state="readonly")
        self.an_method.set("pearson")
        self.an_method.pack(fill="x", pady=(6, 4))
        for text, action in (
            (t("addon.modelling.statistics"), self._an_stats),
            (t("addon.modelling.correlation_matrix"), self._an_corr),
            (t("addon.modelling.distributions_histogram"), self._an_hist),
            (t("addon.modelling.scatter_plot_2_variables"), self._an_scatter),
            (t("addon.modelling.time_series"), self._an_series),
            (t("addon.modelling.trend"), self._an_trend),
            (t("addon.modelling.seasonal_decomposition_day"), lambda: self._an_decompose("day")),
            (t("addon.modelling.seasonal_decomposition_year"), lambda: self._an_decompose("year")),
            (t("addon.modelling.autocorrelation_daily_profile"), self._an_autocorrelation),
        ):
            self._button(side, text, action)
        self.an_text = self._output(main, 11)
        self.an_plot = PlotPanel(main)
        self.an_plot.pack(fill="both", expand=True, pady=(6, 0))

    def _an_data(self, minimum: int = 1) -> tuple[pd.DataFrame, list[str]]:
        columns = self._selected(self.an_list, minimum)
        return self._subset(self.an_start.get(), self.an_end.get()), columns

    def _an_stats(self) -> None:
        data, columns = self._an_data()
        self._write(self.an_text, _table(da.describe(data, columns)))
        self.an_plot.reset()
        self.an_plot.draw()

    def _an_corr(self) -> None:
        data, columns = self._an_data(2)
        matrix = da.correlation(data, columns, self.an_method.get())
        self._write(self.an_text, _table(matrix))
        figure = self.an_plot.reset()
        axes = figure.add_subplot(111)
        image = axes.imshow(matrix.to_numpy(), vmin=-1, vmax=1, cmap="RdBu_r")
        axes.set_xticks(range(len(columns)), columns, rotation=45, ha="right")
        axes.set_yticks(range(len(columns)), columns)
        if len(columns) <= 12:
            for i in range(len(columns)):
                for j in range(len(columns)):
                    axes.text(j, i, f"{matrix.iat[i, j]:.2f}", ha="center", va="center", fontsize=8)
        figure.colorbar(image)
        self.an_plot.draw()

    def _an_hist(self) -> None:
        data, columns = self._an_data()
        columns = columns[:6]
        figure = self.an_plot.reset()
        lines = []
        for index, column in enumerate(columns):
            axes = figure.add_subplot((len(columns) + 1) // 2, 2 if len(columns) > 1 else 1, index + 1)
            axes.hist(data[column].dropna(), bins=40, color="#2E75B6")
            axes.set_title(column, fontsize=9)
            test = da.normality(data[column])
            lines.append(f"{column}: " + (
                f"Shapiro-Wilk W={test[0]:.4f}, p={test[1]:.3g}" if test else t("addon.modelling.insufficient_data")
            ))
        self._write(self.an_text, "\n".join(lines))
        self.an_plot.draw()

    def _an_scatter(self) -> None:
        data, columns = self._an_data(2)
        x_name, y_name = columns[0], columns[1]
        result = da.pair_statistics(data[x_name], data[y_name])
        self._write(self.an_text, (
            f"x = {x_name}, y = {y_name}\nn = {result['n']:.0f}\n"
            f"Pearson r = {result['pearson_r']:.4f} (p = {result['pearson_p']:.3g})\n"
            f"Spearman rho = {result['spearman_rho']:.4f} (p = {result['spearman_p']:.3g})\n"
            f"y = {result['slope']:.5g} * x + {result['intercept']:.5g}"
        ))
        figure = self.an_plot.reset()
        axes = figure.add_subplot(111)
        self._scatter(axes, data[x_name], data[y_name])
        xs = np.array([data[x_name].min(), data[x_name].max()])
        axes.plot(xs, result["slope"] * xs + result["intercept"], color="#C00000")
        axes.set_xlabel(x_name)
        axes.set_ylabel(y_name)
        self.an_plot.draw()

    @staticmethod
    def _scatter(axes: Any, x: pd.Series, y: pd.Series) -> None:
        pair = pd.concat([x, y], axis=1).dropna()
        if len(pair) > MAX_SCATTER_POINTS:
            pair = pair.iloc[np.linspace(0, len(pair) - 1, MAX_SCATTER_POINTS).astype(int)]
        axes.scatter(pair.iloc[:, 0], pair.iloc[:, 1], s=6, alpha=0.4, color="#1F4E78")

    def _an_series(self) -> None:
        data, columns = self._an_data()
        columns = columns[:6]
        figure = self.an_plot.reset()
        shared = None
        for index, column in enumerate(columns):
            axes = figure.add_subplot(len(columns), 1, index + 1, sharex=shared)
            shared = shared or axes
            axes.plot(data.index, data[column], lw=0.8, color="#1F4E78")
            axes.set_ylabel(column, fontsize=8)
        figure.autofmt_xdate()
        self._write(self.an_text, "")
        self.an_plot.draw()

    def _an_trend(self) -> None:
        data, columns = self._an_data()
        column = columns[0]
        result = time_series.trend(data[column])
        self._write(self.an_text, (
            f"{column}\n" + t("addon.modelling.slope") + f": {result['slope_per_day']:.5g} / " + t("addon.modelling.day")
            + f"  ({result['slope_per_year']:.5g} / " + t("addon.modelling.year") + ")\n"
            f"R² = {result['r2']:.4f}, p = {result['p_value']:.3g}\n"
            f"Kendall tau = {result['kendall_tau']:.4f} (p = {result['kendall_p']:.3g})"
        ))
        series = data[column].dropna()
        days = (series.index - series.index[0]).total_seconds() / 86400.0
        figure = self.an_plot.reset()
        axes = figure.add_subplot(111)
        axes.plot(series.index, series, lw=0.7, color="#1F4E78", label=column)
        axes.plot(series.index, result["slope_per_day"] * days + result["intercept"], color="#C00000", label="trend")
        axes.legend()
        figure.autofmt_xdate()
        self.an_plot.draw()

    def _an_decompose(self, period: str) -> None:
        data, columns = self._an_data()
        parts = time_series.decompose(data[columns[0]], period)
        figure = self.an_plot.reset()
        shared = None
        for index, name in enumerate(parts.columns):
            axes = figure.add_subplot(4, 1, index + 1, sharex=shared)
            shared = shared or axes
            axes.plot(parts.index, parts[name], lw=0.7, color="#1F4E78")
            axes.set_ylabel(name, fontsize=8)
        figure.autofmt_xdate()
        share = 1 - parts["residual"].var() / parts["observed"].var() if parts["observed"].var() > 0 else float("nan")
        self._write(self.an_text, f"{columns[0]}: " + t("addon.modelling.variance_explained_by_trend_season") + f" = {share:.3f}")
        self.an_plot.draw()

    def _an_autocorrelation(self) -> None:
        data, columns = self._an_data()
        series = data[columns[0]]
        correlation = time_series.autocorrelation(series, 96)
        profile = time_series.diurnal_profile(series)
        figure = self.an_plot.reset()
        first = figure.add_subplot(121)
        first.bar(correlation.index, correlation.to_numpy(), color="#2E75B6")
        first.set_title(t("addon.modelling.autocorrelation_lag_in_steps"), fontsize=9)
        second = figure.add_subplot(122)
        second.errorbar(profile.index, profile["mean"], yerr=profile["std"], color="#C00000", capsize=2)
        second.set_title(t("addon.modelling.mean_daily_profile_hour"), fontsize=9)
        self._write(self.an_text, _table(profile))
        self.an_plot.draw()

    # ---------- Tab 2: Model Builder ----------
    def _build_builder_tab(self) -> None:
        side, main = self._side_and_main(self._tab(t("addon.modelling.model_builder")), 330)
        ttk.Label(side, text=t("addon.modelling.model_type")).pack(anchor="w")
        self.mb_kind = self._combo(side, [
            t("addon.modelling.linear_multiple_variables"),
            t("addon.modelling.polynomial_1_variable"),
            t("addon.modelling.custom_formula_non_linear"),
        ])
        self.mb_kind.current(0)
        ttk.Label(side, text=t("addon.modelling.dependent_variable_target")).pack(anchor="w")
        self.mb_target = self._combo(side)
        ttk.Label(side, text=t("addon.modelling.independent_variables_linear_polynomial")).pack(anchor="w")
        self.mb_inputs = self._listbox(side, 9)
        self.mb_degree = self._entry(side, t("addon.modelling.polynomial_degree"), "2")
        self.mb_train = self._entry(side, t("addon.modelling.training_share"), "100")
        ttk.Label(side, text=t("addon.modelling.formula_e_g_a_temp_b")).pack(anchor="w", pady=(6, 0))
        self.mb_formula = ttk.Entry(side)
        self.mb_formula.pack(fill="x")
        self.mb_start = self._entry(side, t("addon.modelling.start_values_a_1_b_0"), "", 16)
        self.mb_fit = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text=t("addon.modelling.fit_parameters_to_measured_data"), variable=self.mb_fit).pack(anchor="w")
        self._button(side, t("addon.modelling.build_and_fit_model"), self._mb_build)
        self.mb_text = self._output(main, 12)
        self.mb_plot = PlotPanel(main)
        self.mb_plot.pack(fill="both", expand=True, pady=(6, 0))
        self._write(self.mb_text, t("addon.modelling.time_variables_time_days_hour_and"))

    def _mb_build(self) -> None:
        kind = self.mb_kind.current()
        target = self._key(self.mb_target.get()) if self.mb_target.get() else None
        fraction = _number(self.mb_train.get(), t("addon.modelling.training_share_2")) / 100.0
        if not 0.05 <= fraction <= 1.0:
            raise ValueError(t("addon.modelling.training_share_must_be_between_5"))
        if kind in (0, 1) and not target:
            raise ValueError(t("addon.modelling.choose_a_dependent_variable"))
        if kind == 0:
            inputs = self._selected(self.mb_inputs)
            if target in inputs:
                raise ValueError(t("addon.modelling.the_target_cannot_also_be_an"))
            spec = regression.fit_linear(self.frame, inputs, target, fraction)
        elif kind == 1:
            inputs = self._selected(self.mb_inputs)
            if len(inputs) != 1 or inputs[0] == target:
                raise ValueError(t("addon.modelling.choose_exactly_one_independent_variable_different"))
            spec = regression.fit_polynomial(self.frame, inputs[0], target, int(_number(self.mb_degree.get(), t("addon.modelling.degree"))), fraction)
        else:
            spec = regression.fit_formula(
                self.frame, self.mb_formula.get(), target, self._parse_start_values(self.mb_start.get()),
                self.mb_fit.get(), fraction,
            )
        self._set_model(spec)
        lines = [f"{spec.name}", spec.expression, ""]
        for name, value in spec.params.items():
            error = spec.errors.get(name)
            lines.append(f"  {name} = {value:.6g}" + (f" ± {error:.3g}" if error is not None and np.isfinite(error) else ""))
        if spec.target:
            lines += ["", self._metrics_text(spec, spec.target)]
        self._write(self.mb_text, "\n".join(lines))
        self._draw_comparison(self.mb_plot, spec, spec.target, self.frame)

    @staticmethod
    def _parse_start_values(text: str) -> dict[str, float]:
        values: dict[str, float] = {}
        for part in text.replace(";", ",").split(","):
            if not part.strip():
                continue
            name, sep, value = part.partition("=")
            if not sep:
                raise ValueError(t("addon.modelling.start_values_use_the_form_a"))
            values[name.strip()] = _number(value, name.strip())
        return values

    def _metrics_text(self, spec: ModelSpec, target: str) -> str:
        parts = []
        for label, subset in self._subsets(spec):
            try:
                pair = validation.paired(subset[target], spec.predict(subset))
            except ValueError:
                continue
            values = validation.metrics(pair)
            parts.append(f"[{label}] n={values['n']:.0f}  R²={values['R2']:.4f}  RMSE={values['RMSE']:.4g}  MAE={values['MAE']:.4g}  Bias={values['Bias']:.4g}")
        return "\n".join(parts)

    def _subsets(self, spec: ModelSpec) -> list[tuple[str, pd.DataFrame]]:
        if spec.train_end is None:
            return [(t("addon.modelling.all_data"), self.frame)]
        return [
            (t("addon.modelling.training"), self.frame[self.frame.index <= spec.train_end]),
            (t("addon.modelling.test"), self.frame[self.frame.index > spec.train_end]),
        ]

    def _draw_comparison(self, panel: PlotPanel, spec: ModelSpec, target: str | None, data: pd.DataFrame) -> None:
        predicted = spec.predict(data)
        figure = panel.reset()
        if target and target in data.columns:
            observed = data[target]
            first = figure.add_subplot(121)
            first.plot(data.index, observed, lw=0.7, color="#1F4E78", label=target)
            first.plot(data.index, predicted, lw=0.7, color="#C00000", label=t("addon.modelling.model"))
            first.legend(fontsize=8)
            figure.autofmt_xdate()
            second = figure.add_subplot(122)
            self._scatter(second, observed, predicted)
            both = pd.concat([observed, predicted]).dropna()
            if not both.empty:
                second.plot([both.min(), both.max()], [both.min(), both.max()], color="#7F7F7F", ls="--")
            second.set_xlabel(t("addon.modelling.measured"))
            second.set_ylabel(t("addon.modelling.model"))
        else:
            axes = figure.add_subplot(111)
            axes.plot(data.index, predicted, lw=0.8, color="#C00000")
            axes.set_ylabel(f"{spec.name} [{spec.unit}]" if spec.unit else spec.name)
            figure.autofmt_xdate()
        panel.draw()

    # ---------- Tab 3: Physical Models ----------
    def _build_physical_tab(self) -> None:
        side, main = self._side_and_main(self._tab(t("addon.modelling.physical_models")), 360)
        ttk.Label(side, text=t("addon.modelling.model_2")).pack(anchor="w")
        self._phys_keys = list(PHYSICAL_MODELS)
        self.ph_model = self._combo(side, [t(PHYSICAL_MODELS[k].name) for k in self._phys_keys], 38)
        self.ph_model.bind("<<ComboboxSelected>>", lambda _e: self._phys_select())
        self.ph_description = ttk.Label(side, wraplength=330, justify="left")
        self.ph_description.pack(fill="x", pady=(0, 8))
        self.ph_inputs_frame = ttk.LabelFrame(side, text=t("addon.modelling.map_inputs_to_measured_variables"), padding=6)
        self.ph_inputs_frame.pack(fill="x")
        self.ph_params_frame = ttk.LabelFrame(side, text=t("addon.modelling.parameters"), padding=6)
        self.ph_params_frame.pack(fill="x", pady=8)
        ttk.Label(side, text=t("addon.modelling.measured_variable_to_compare_with_optional")).pack(anchor="w")
        self.ph_target = self._combo(side, [""] + self.labels, 38)
        self._button(side, t("addon.modelling.apply_model"), self._phys_apply)
        self.ph_text = self._output(main, 6)
        self.ph_plot = PlotPanel(main)
        self.ph_plot.pack(fill="both", expand=True, pady=(6, 0))
        self._ph_input_boxes: dict[str, ttk.Combobox] = {}
        self._ph_param_entries: dict[str, ttk.Entry] = {}
        self.ph_model.current(0)
        self._phys_select()

    def _current_physical(self) -> PhysicalModel:
        return PHYSICAL_MODELS[self._phys_keys[self.ph_model.current()]]

    def _phys_select(self) -> None:
        model = self._current_physical()
        self.ph_description.configure(text=t(model.description) + f"  [{model.unit}]")
        for frame in (self.ph_inputs_frame, self.ph_params_frame):
            for child in frame.winfo_children():
                child.destroy()
        self._ph_input_boxes, self._ph_param_entries = {}, {}
        for key, label, unit in model.inputs:
            ttk.Label(self.ph_inputs_frame, text=t(label) + f" [{unit}]").pack(anchor="w")
            box = ttk.Combobox(self.ph_inputs_frame, values=self.labels, state="readonly", width=38)
            box.pack(fill="x", pady=(0, 4))
            self._ph_input_boxes[key] = box
        for key, label, default in model.params:
            self._ph_param_entries[key] = self._entry(self.ph_params_frame, t(label), f"{default:g}", 10)

    def _phys_apply(self) -> None:
        model = self._current_physical()
        mapping = {key: self._key(box.get()) for key, box in self._ph_input_boxes.items() if box.get()}
        params = {key: _number(entry.get(), key) for key, entry in self._ph_param_entries.items()}
        run_physical_model(model, mapping, params, self.frame)
        target = self._key(self.ph_target.get()) if self.ph_target.get() else None
        spec = ModelSpec(
            name=t(model.name), kind="physical", inputs=list(dict.fromkeys(mapping.values())), target=target,
            predict=lambda f: run_physical_model(model, mapping, params, f), params=params, unit=model.unit,
            expression=", ".join(f"{k}={v}" for k, v in mapping.items()),
            definition={"physical_key": model.key, "mapping": mapping},
        )
        self._set_model(spec)
        result = spec.predict(self.frame)
        self._write(self.ph_text, f"{spec.name} [{model.unit}]\n" + _table(result.describe()))
        self._draw_comparison(self.ph_plot, spec, target, self.frame)

    # ---------- Tab 4: Model Evaluation ----------
    def _build_evaluation_tab(self) -> None:
        side, main = self._side_and_main(self._tab(t("addon.modelling.model_evaluation")))
        ttk.Label(side, text=t("addon.modelling.measured_variable")).pack(anchor="w")
        self.ev_target = self._combo(side)
        ttk.Label(side, text=t("addon.modelling.dataset")).pack(anchor="w")
        self.ev_subset = self._combo(side, [t("addon.modelling.all_data"), t("addon.modelling.training"), t("addon.modelling.test")])
        self.ev_subset.current(0)
        self._button(side, t("addon.modelling.evaluate_active_model"), self._ev_run)
        self.ev_text = self._output(main, 14)
        self.ev_plot = PlotPanel(main)
        self.ev_plot.pack(fill="both", expand=True, pady=(6, 0))

    def _ev_run(self) -> None:
        spec = self._require_model()
        if not self.ev_target.get():
            raise ValueError(t("addon.modelling.choose_a_measured_variable"))
        target = self._key(self.ev_target.get())
        choice = self.ev_subset.current()
        data = self.frame
        if choice > 0:
            if spec.train_end is None:
                raise ValueError(t("addon.modelling.this_model_has_no_train_test"))
            data = data[data.index <= spec.train_end] if choice == 1 else data[data.index > spec.train_end]
        pair = validation.paired(data[target], spec.predict(data))
        values, residuals = validation.metrics(pair), validation.residual_analysis(pair)
        lines = [f"{spec.name}  vs  {target}", ""]
        lines += [f"{k:<22}{v:.5g}" for k, v in values.items()] + [""]
        lines += [t("addon.modelling.residual") + f" {k:<18}{v:.5g}" for k, v in residuals.items()]
        self._write(self.ev_text, "\n".join(lines))
        figure = self.ev_plot.reset()
        series = figure.add_subplot(221)
        series.plot(pair.index, pair["observed"], lw=0.6, color="#1F4E78", label=t("addon.modelling.measured"))
        series.plot(pair.index, pair["predicted"], lw=0.6, color="#C00000", label=t("addon.modelling.model"))
        series.legend(fontsize=7)
        scatter = figure.add_subplot(222)
        self._scatter(scatter, pair["observed"], pair["predicted"])
        low, high = pair[["observed", "predicted"]].to_numpy().min(), pair[["observed", "predicted"]].to_numpy().max()
        scatter.plot([low, high], [low, high], color="#7F7F7F", ls="--")
        scatter.set_xlabel(t("addon.modelling.measured"), fontsize=8)
        scatter.set_ylabel(t("addon.modelling.model"), fontsize=8)
        histogram = figure.add_subplot(223)
        histogram.hist(pair["residual"], bins=40, color="#2E75B6")
        histogram.set_title(t("addon.modelling.residuals"), fontsize=8)
        against = figure.add_subplot(224)
        self._scatter(against, pair["predicted"], pair["residual"])
        against.axhline(0, color="#C00000", lw=0.8)
        against.set_title(t("addon.modelling.residual_vs_model"), fontsize=8)
        figure.autofmt_xdate()
        self.ev_plot.draw()

    # ---------- Tab 5: Simulation & Prediction ----------
    def _build_simulation_tab(self) -> None:
        side, main = self._side_and_main(self._tab(t("addon.modelling.simulation_prediction")), 380)
        ttk.Label(side, text=t("addon.modelling.change_input_variables_of_the_active"), wraplength=350).pack(anchor="w")
        self.sim_rows_frame = ttk.Frame(side)
        self.sim_rows_frame.pack(fill="x", pady=6)
        self.sim_start = self._entry(side, t("addon.modelling.from_yyyy_mm_dd"), self.frame.index[0].strftime("%Y-%m-%d"))
        self.sim_end = self._entry(side, t("addon.modelling.to"), self.frame.index[-1].strftime("%Y-%m-%d"))
        self._button(side, t("addon.modelling.simulate_scenario"), self._sim_run)
        box = ttk.LabelFrame(side, text=t("addon.modelling.sensitivity_analysis"), padding=6)
        box.pack(fill="x", pady=(10, 0))
        self.sim_variable = ttk.Combobox(box, state="readonly", width=34)
        self.sim_variable.pack(fill="x", pady=(0, 4))
        self.sim_sweep_mode = ttk.Combobox(box, values=self._mode_labels(), state="readonly", width=20)
        self.sim_sweep_mode.current(0)
        self.sim_sweep_mode.pack(fill="x", pady=(0, 4))
        self.sim_from = self._entry(box, t("addon.modelling.from_value"), "-5")
        self.sim_to = self._entry(box, t("addon.modelling.to_value"), "5")
        self.sim_steps = self._entry(box, t("addon.modelling.steps"), "11")
        self._button(box, t("addon.modelling.compute_sensitivity"), self._sim_sensitivity)
        self.sim_text = self._output(main, 10)
        self.sim_plot = PlotPanel(main)
        self.sim_plot.pack(fill="both", expand=True, pady=(6, 0))
        self._sim_rows: dict[str, tuple[ttk.Combobox, ttk.Entry]] = {}
        self._sim_refresh()

    @staticmethod
    def _mode_labels() -> list[str]:
        return [t("addon.modelling.add"), t("addon.modelling.multiply"), t("addon.modelling.fixed_value")]

    def _sim_refresh(self) -> None:
        for child in self.sim_rows_frame.winfo_children():
            child.destroy()
        self._sim_rows = {}
        if self.model is None:
            ttk.Label(self.sim_rows_frame, text=t("addon.modelling.build_or_choose_a_model_first")).pack(anchor="w")
            self.sim_variable.configure(values=[])
            return
        for variable in self.model.inputs:
            row = ttk.Frame(self.sim_rows_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=variable, width=18).pack(side="left")
            mode = ttk.Combobox(row, values=["—"] + self._mode_labels(), state="readonly", width=16)
            mode.current(0)
            mode.pack(side="left", padx=4)
            value = ttk.Entry(row, width=8)
            value.insert(0, "0")
            value.pack(side="left")
            self._sim_rows[variable] = (mode, value)
        self.sim_variable.configure(values=self.model.inputs)
        self.sim_variable.current(0)

    def _sim_scenario(self) -> dict[str, tuple[str, float]]:
        scenario = {}
        for variable, (mode, value) in self._sim_rows.items():
            if mode.current() > 0:
                scenario[variable] = (("add", "mul", "set")[mode.current() - 1], _number(value.get(), variable))
        if not scenario:
            raise ValueError(t("addon.modelling.set_at_least_one_variable_e"))
        return scenario

    def _sim_run(self) -> None:
        spec = self._require_model()
        scenario = self._sim_scenario()
        data = self._subset(self.sim_start.get(), self.sim_end.get())
        result = simulate(spec.predict, data, scenario)
        mean_base, mean_new = result["baseline"].mean(), result["scenario"].mean()
        change = (mean_new - mean_base) / abs(mean_base) * 100 if mean_base else float("nan")
        unit = f" {spec.unit}" if spec.unit else ""
        lines = [f"{spec.name}", ", ".join(f"{v}: {m} {x:g}" for v, (m, x) in scenario.items()), ""]
        lines += [
            t("addon.modelling.mean_baseline") + f": {mean_base:.5g}{unit}",
            t("addon.modelling.mean_scenario") + f": {mean_new:.5g}{unit}",
            t("addon.modelling.difference_2") + f": {mean_new - mean_base:+.5g}{unit} ({change:+.2f}%)",
            t("addon.modelling.sum_baseline_scenario") + f": {result['baseline'].sum():.6g} / {result['scenario'].sum():.6g}",
        ]
        self._write(self.sim_text, "\n".join(lines))
        figure = self.sim_plot.reset()
        top = figure.add_subplot(211)
        top.plot(result.index, result["baseline"], lw=0.7, color="#1F4E78", label=t("addon.modelling.baseline"))
        top.plot(result.index, result["scenario"], lw=0.7, color="#C00000", label=t("addon.modelling.scenario"))
        top.legend(fontsize=8)
        bottom = figure.add_subplot(212, sharex=top)
        bottom.plot(result.index, result["difference"], lw=0.7, color="#548235")
        bottom.set_ylabel(t("addon.modelling.difference"), fontsize=8)
        figure.autofmt_xdate()
        self.sim_plot.draw()

    def _sim_sensitivity(self) -> None:
        spec = self._require_model()
        if not self.sim_variable.get():
            raise ValueError(t("addon.modelling.choose_a_variable"))
        data = self._subset(self.sim_start.get(), self.sim_end.get())
        table = sensitivity(
            spec.predict, data, self.sim_variable.get(), ("add", "mul", "set")[self.sim_sweep_mode.current()],
            _number(self.sim_from.get()), _number(self.sim_to.get()), int(_number(self.sim_steps.get())),
        )
        self._write(self.sim_text, _table(table))
        figure = self.sim_plot.reset()
        axes = figure.add_subplot(111)
        axes.plot(table["value"], table["mean"], marker="o", color="#1F4E78")
        axes.set_xlabel(f"{self.sim_variable.get()} ({self.sim_sweep_mode.get()})")
        axes.set_ylabel(t("addon.modelling.mean_model_output"))
        axes.grid(alpha=0.3)
        self.sim_plot.draw()

    # ---------- gedeeld ----------
    def _require_model(self) -> ModelSpec:
        if self.model is None:
            raise ValueError(t("addon.modelling.there_is_no_active_model_yet"))
        return self.model

    def _model_dialog_dir(self) -> str | None:
        if self.model_dir is None:
            return None
        self.model_dir.mkdir(parents=True, exist_ok=True)
        return str(self.model_dir)

    def _save_model(self) -> None:
        spec = self._require_model()
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in spec.name).strip("_") or "model"
        path = filedialog.asksaveasfilename(
            parent=self, title=t("addon.modelling.save_model"), initialdir=self._model_dialog_dir(),
            initialfile=f"{safe_name}{model_store.SUFFIX}", defaultextension=model_store.SUFFIX,
            filetypes=[(t("addon.modelling.model_definition"), f"*{model_store.SUFFIX}"), (t("addon.modelling.all_files"), "*.*")],
        )
        if path:
            model_store.save_model(spec, path)
            self.status.set(t("addon.modelling.model_saved") + f": {Path(path).name}")

    def _load_model(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title=t("addon.modelling.load_model"), initialdir=self._model_dialog_dir(),
            filetypes=[(t("addon.modelling.model_definition"), f"*{model_store.SUFFIX}"), (t("addon.modelling.all_files"), "*.*")],
        )
        if not path:
            return
        spec = model_store.load_model(path, set(self.frame.columns))
        self._set_model(spec)
        lines = [spec.name, spec.expression, ""] + [
            f"  {k} = {v:.6g}" for k, v in spec.params.items()
        ]
        if spec.target:
            lines += ["", self._metrics_text(spec, spec.target)] if spec.target in self.frame.columns else []
        self._write(self.mb_text, "\n".join(lines))
        self._draw_comparison(self.mb_plot, spec, spec.target, self.frame)
        self.notebook.select(1)

    def _set_model(self, spec: ModelSpec) -> None:
        self.model = spec
        split = t("addon.modelling.train_test_split") if spec.train_end is not None else ""
        self.status.set(t("addon.modelling.active_model") + f": {spec.name}{split}")
        if spec.target:
            self.ev_target.set(self._label(spec.target))
        self._sim_refresh()


def open_modelling_window(
    parent: tk.Misc, data: pd.DataFrame, header_rows: int, source_name: str, model_dir: Path | None = None,
) -> ModellingWindow | None:
    try:
        frame = da.numeric_frame(data, header_rows)
    except ValueError as exc:
        messagebox.showerror(t("addon.modelling.analysis_and_modelling"), str(exc), parent=parent)
        return None
    return ModellingWindow(parent, frame, source_name, model_dir)
