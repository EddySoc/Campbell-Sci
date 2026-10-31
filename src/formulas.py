from __future__ import annotations

import ast
import operator
import re
import shutil
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

FORMULA_HEADER = (
    "# name;formula\n"
    "# Excel-achtige formules. Kolommen: [Kolomnaam]. Dagfuncties: DAGMIN, DAGMAX, DAGSOM, DAGGEM.\n"
)


class FormulaError(ValueError):
    pass


def formula_candidates(logger: str, location: str | None, config_dir: str | Path) -> list[Path]:
    base = Path(config_dir)
    candidates = []
    if location:
        candidates.append(base / f"{logger.upper()}_{location.upper()}.formuladef")
    candidates.append(base / f"{logger.upper()}.formuladef")
    if logger.upper() == "BRAS":
        candidates.append(base / "BM.formuladef")
    return candidates


def formula_path(logger: str, location: str | None, config_dir: str | Path) -> Path:
    """Pad waarnaar wordt opgeslagen: bestaand bestand, anders de meest specifieke naam."""
    candidates = formula_candidates(logger, location, config_dir)
    return next((path for path in candidates if path.is_file()), candidates[0])


def load_formulas(logger: str, location: str | None, config_dir: str | Path) -> list[dict[str, str]]:
    path = formula_path(logger, location, config_dir)
    if not path.is_file():
        return []
    formulas = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, expression = line.partition(";")
        if not separator or not name.strip() or not expression.strip():
            raise ValueError(f"Ongeldige formule in {path.name}:{number}")
        formulas.append({"name": name.strip(), "expression": expression.strip()})
    return formulas


def backup_original(path: str | Path) -> None:
    """Bewaart de huidige versie als <bestand>.bak (vorige versie); een bestaande backup wordt overschreven."""
    source = Path(path)
    backup = source.with_name(source.name + ".bak")
    if source.is_file():
        shutil.copy2(source, backup)


def save_formulas(path: str | Path, formulas: list[dict[str, str]]) -> None:
    target = Path(path)
    backup_original(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{f['name'].replace(';', ',').strip()};{' '.join(f['expression'].split())}"
        for f in formulas
    ]
    target.write_text(FORMULA_HEADER + "\n".join(lines) + "\n", encoding="utf-8")


_REFERENCE = re.compile(r"\[([^\]]+)\]")
_PLACEHOLDER = re.compile(r"__c(\d+)__")
_CONSTANTS = {"PI": np.pi, "TRUE": True, "FALSE": False}
_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
}
_COMPARE = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}
ALIASES = {
    "SOM": "SUM", "GEMIDDELDE": "AVERAGE", "ALS": "IF", "EN": "AND", "OF": "OR", "NIET": "NOT",
    "WORTEL": "SQRT", "AFRONDEN": "ROUND", "MACHT": "POWER", "REST": "MOD", "HELE": "INT",
    "DAYMIN": "DAGMIN", "DAYMAX": "DAGMAX", "DAYSUM": "DAGSOM", "DAYAVG": "DAGGEM", "DAYAVERAGE": "DAGGEM",
    "VERSCHIL": "DIFF", "GLIJDEND": "ROLLAVG", "MULTIPLY": "PRODUCT",
    "AFTREKKEN": "SUBTRACT", "VERMENIGVULDIGEN": "PRODUCT", "DELEN": "DIVIDE",
}

# naam -> beschrijving (voor de editor)
FUNCTION_HELP = {
    "DAGMIN": "DAGMIN([kolom]) - minimum per dag",
    "DAGMAX": "DAGMAX([kolom]) - maximum per dag",
    "DAGSOM": "DAGSOM([kolom]) - totaal per dag",
    "DAGGEM": "DAGGEM([kolom]) - gemiddelde per dag",
    "SUM": "SUM(a; b; ...) - som per rij (SOM)",
    "SUBTRACT": "SUBTRACT(a; b; ...) - trek waarden per rij van elkaar af (AFTREKKEN)",
    "PRODUCT": "PRODUCT(a; b; ...) - vermenigvuldig waarden per rij (MULTIPLY)",
    "DIVIDE": "DIVIDE(a; b; ...) - deel waarden per rij achtereenvolgens (DELEN)",
    "MIN": "MIN(a; b; ...) - minimum per rij",
    "MAX": "MAX(a; b; ...) - maximum per rij",
    "AVERAGE": "AVERAGE(a; b; ...) - gemiddelde per rij (GEMIDDELDE)",
    "IF": "IF(voorwaarde; dan; anders) (ALS)",
    "AND": "AND(a; b; ...) (EN)",
    "OR": "OR(a; b; ...) (OF)",
    "NOT": "NOT(a) (NIET)",
    "ABS": "ABS(x)",
    "SQRT": "SQRT(x) (WORTEL)",
    "ROUND": "ROUND(x; decimalen) (AFRONDEN)",
    "INT": "INT(x) - naar beneden afronden (HELE)",
    "POWER": "POWER(x; y) of x^y (MACHT)",
    "MOD": "MOD(x; y) (REST)",
    "LN": "LN(x)",
    "LOG10": "LOG10(x)",
    "EXP": "EXP(x)",
    "SIN": "SIN(x) - radialen",
    "COS": "COS(x) - radialen",
    "TAN": "TAN(x) - radialen",
    "RADIANS": "RADIANS(graden)",
    "DEGREES": "DEGREES(radialen)",
    "DIFF": "DIFF(x) - verschil met vorige rij (VERSCHIL)",
    "ROLLAVG": "ROLLAVG(x; n) - voortschrijdend gemiddelde over n rijen (GLIJDEND)",
    "STAP": "STAP() - tijdstap van de data in uren",
    "UUR": "UUR() - tijdstip van de rij als decimaal uur (0-24)",
    "ZONOP": "ZONOP(breedte; lengte; utc-verschil) - zonsopkomst in decimaal uur",
    "ZONONDER": "ZONONDER(breedte; lengte; utc-verschil) - zonsondergang in decimaal uur",
}

_MACRO_HEAD = re.compile(r"^([A-Za-z_]\w*)\s*(?:\(([^)]*)\))?$")
MAX_CALL_DEPTH = 20

FUNCDEF_HEADER = (
    "# Eigen functies en constanten (worden voor alle loggers geladen)\n"
    "# Constante:  NAAM = waarde of formule\n"
    "# Functie:    NAAM(param1; param2) = formule   (parameters als [param1] of param1)\n"
)


def parse_funcdefs(text: str, source: str = "funcdef") -> dict[str, dict[str, Any]]:
    macros: dict[str, dict[str, Any]] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, separator, body = line.partition("=")
        match = _MACRO_HEAD.match(head.strip())
        if not separator or not match or not body.strip():
            raise ValueError(f"Ongeldige definitie in {source}:{number}")
        name = match.group(1).upper()
        if name in FUNCTION_HELP or name in ALIASES or name in _CONSTANTS:
            raise ValueError(f"'{match.group(1)}' is een ingebouwde naam ({source}:{number})")
        params = None if match.group(2) is None else [
            p.strip().upper() for p in re.split(r"[;,]", match.group(2)) if p.strip()
        ]
        macros[name] = {"name": match.group(1), "params": params, "body": body.strip()}
    return macros


def load_funcdefs(directory: str | Path) -> dict[str, dict[str, Any]]:
    macros: dict[str, dict[str, Any]] = {}
    base = Path(directory)
    if base.is_dir():
        for path in sorted(base.glob("*.funcdef")):
            macros.update(parse_funcdefs(path.read_text(encoding="utf-8"), path.name))
    return macros


def macro_help(macros: dict[str, dict[str, Any]]) -> dict[str, str]:
    result = {}
    for macro in macros.values():
        if macro["params"] is None:
            result[macro["name"]] = f"{macro['name']} - eigen constante"
        else:
            result[macro["name"]] = f"{macro['name']}({'; '.join(macro['params'])}) - eigen functie"
    return result


def _preprocess(expression: str) -> tuple[str, list[str]]:
    references: list[str] = []

    def replace(match: re.Match[str]) -> str:
        references.append(match.group(1).strip())
        return f"__c{len(references) - 1}__"

    text = expression.strip().lstrip("=").strip()
    text = _REFERENCE.sub(replace, text)
    text = text.replace(";", ",").replace("^", "**").replace("<>", "!=")
    text = re.sub(r"(?<![<>=!])=(?!=)", "==", text)
    return text, references


class _Evaluator:
    def __init__(
        self,
        data: pd.DataFrame,
        header_rows: int,
        references: list[str],
        macros: dict[str, dict[str, Any]] | None = None,
    ):
        self.measure = data.iloc[header_rows:]
        self.index = self.measure.index
        self.references = references
        self.macros = macros or {}
        self.params: dict[str, Any] = {}
        self.depth = 0
        self.lookup = {str(column).strip().upper(): column for column in data.columns}
        if "TMSTAMP" in self.lookup:
            self.lookup.setdefault("TIMESTAMP", self.lookup["TMSTAMP"])
        self._times: pd.Series | None = None
        self._day: pd.Series | None = None
        self.functions: dict[str, Callable[..., Any]] = self._build_functions()

    def column(self, name: str) -> Any:
        key = name.strip().upper()
        if key in self.params:
            return self.params[key]
        column = self.lookup.get(key)
        if column is None:
            raise FormulaError(f"Kolom '{name}' bestaat niet.")
        return pd.to_numeric(self.measure[column], errors="coerce").astype(float)

    def times(self) -> pd.Series:
        if self._times is None:
            self._times = pd.to_datetime(self.measure.iloc[:, 0], format="mixed", errors="coerce")
        return self._times

    def day(self) -> pd.Series:
        if self._day is None:
            self._day = self.times().dt.normalize()
        return self._day

    def _step(self) -> np.float64:
        seconds = self.times().diff().dt.total_seconds().median()
        if pd.isna(seconds) or seconds <= 0:
            raise FormulaError("De tijdstap van de data kan niet worden bepaald.")
        return np.float64(seconds / 3600)

    def _hour(self) -> pd.Series:
        times = self.times()
        return times.dt.hour + times.dt.minute / 60 + times.dt.second / 3600

    def _sun(self, rising: bool, latitude: Any, longitude: Any, offset: Any = 0) -> pd.Series:
        """Zonsopkomst/-ondergang (NOAA-benadering), in decimaal uur lokale logger-tijd."""
        doy = self.times().dt.dayofyear.to_numpy(dtype=float)
        g = 2 * np.pi / 365 * (doy - 1)
        equation = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                             - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
        declination = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
                       + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
        lat = np.radians(float(latitude))
        cos_ha = np.cos(np.radians(90.833)) / (np.cos(lat) * np.cos(declination)) - np.tan(lat) * np.tan(declination)
        ha = np.degrees(np.arccos(np.where(np.abs(cos_ha) <= 1, cos_ha, np.nan)))
        minutes = 720 - 4 * (float(longitude) + (ha if rising else -ha)) - equation
        return pd.Series(minutes / 60 + float(offset), index=self.index)

    def series(self, value: Any) -> pd.Series:
        return value if isinstance(value, pd.Series) else pd.Series(value, index=self.index)

    def boolean(self, value: Any) -> Any:
        if isinstance(value, pd.Series):
            return (value.fillna(0) != 0).to_numpy()
        return bool(value)

    def _daily(self, how: str) -> Callable[[Any], pd.Series]:
        def calculate(value: Any) -> pd.Series:
            grouped = self.series(value).astype(float).groupby(self.day())
            result = grouped.transform(how)
            return result.where(grouped.transform("count") > 0)
        return calculate

    def _across(self, how: str) -> Callable[..., pd.Series]:
        def calculate(*values: Any) -> pd.Series:
            if not values:
                raise FormulaError("Minstens één argument nodig.")
            if how in {"subtract", "divide"} and len(values) < 2:
                raise FormulaError("Minstens twee argumenten nodig.")
            if how in {"subtract", "multiply", "divide"}:
                result = self.series(values[0]).astype(float)
                for value in values[1:]:
                    operand = self.series(value).astype(float)
                    if how == "subtract":
                        result = result - operand
                    elif how == "multiply":
                        result = result * operand
                    else:
                        result = result / operand
                return result
            frame = pd.concat([self.series(v).astype(float) for v in values], axis=1)
            if how == "sum":
                return frame.sum(axis=1, min_count=1)
            return getattr(frame, how)(axis=1)
        return calculate

    def _if(self, condition: Any, then: Any, otherwise: Any) -> pd.Series:
        values = np.where(self.boolean(condition), np.asarray(then), np.asarray(otherwise))
        return pd.Series(values, index=self.index)

    def _logic(self, combine: Callable[[Any, Any], Any]) -> Callable[..., Any]:
        def calculate(*values: Any) -> Any:
            if not values:
                raise FormulaError("Minstens één argument nodig.")
            result = self.boolean(values[0])
            for value in values[1:]:
                result = combine(result, self.boolean(value))
            return pd.Series(result, index=self.index) if not np.isscalar(result) else result
        return calculate

    def _round(self, value: Any, digits: Any = 0) -> Any:
        return np.round(value, int(digits))

    def _rolling(self, value: Any, window: Any) -> pd.Series:
        return self.series(value).astype(float).rolling(max(1, int(window)), min_periods=1).mean()

    def _build_functions(self) -> dict[str, Callable[..., Any]]:
        functions: dict[str, Callable[..., Any]] = {
            "DAGMIN": self._daily("min"), "DAGMAX": self._daily("max"),
            "DAGSOM": self._daily("sum"), "DAGGEM": self._daily("mean"),
            "SUM": self._across("sum"), "MIN": self._across("min"),
            "MAX": self._across("max"), "AVERAGE": self._across("mean"),
            "SUBTRACT": self._across("subtract"), "PRODUCT": self._across("multiply"),
            "DIVIDE": self._across("divide"),
            "IF": self._if,
            "AND": self._logic(np.logical_and), "OR": self._logic(np.logical_or),
            "NOT": lambda value: ~np.asarray(self.boolean(value)) if isinstance(value, pd.Series) else not value,
            "ABS": np.abs, "SQRT": np.sqrt, "LN": np.log, "LOG10": np.log10, "EXP": np.exp,
            "SIN": np.sin, "COS": np.cos, "TAN": np.tan, "RADIANS": np.radians, "DEGREES": np.degrees,
            "INT": np.floor, "ROUND": self._round, "POWER": operator.pow, "MOD": operator.mod,
            "DIFF": lambda value: self.series(value).astype(float).diff(),
            "ROLLAVG": self._rolling,
            "STAP": self._step, "UUR": self._hour,
            "ZONOP": lambda *args: self._sun(True, *args),
            "ZONONDER": lambda *args: self._sun(False, *args),
        }
        return functions

    def _call_macro(self, macro: dict[str, Any], arguments: list[Any]) -> Any:
        params = macro["params"] or []
        if len(arguments) != len(params):
            raise FormulaError(f"{macro['name']} verwacht {len(params)} argument(en).")
        if self.depth >= MAX_CALL_DEPTH:
            raise FormulaError(f"Te diepe functie-aanroep bij {macro['name']} (recursie?).")
        text, references = _preprocess(macro["body"])
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError as exc:
            raise FormulaError(f"Syntaxfout in definitie van {macro['name']}.") from exc
        saved = (self.params, self.references)
        self.params, self.references = dict(zip(params, arguments)), references
        self.depth += 1
        try:
            return self.visit(tree)
        finally:
            self.params, self.references = saved
            self.depth -= 1

    def visit(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self.visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return node.value
            if isinstance(node.value, (int, float)):
                return np.float64(node.value)
            raise FormulaError("Alleen getallen zijn toegestaan als constante.")
        if isinstance(node, ast.Name):
            match = _PLACEHOLDER.fullmatch(node.id)
            if match:
                return self.column(self.references[int(match.group(1))])
            if node.id.upper() in self.params:
                return self.params[node.id.upper()]
            macro = self.macros.get(node.id.upper())
            if macro is not None and macro["params"] is None:
                return self._call_macro(macro, [])
            if node.id.upper() in _CONSTANTS:
                return _CONSTANTS[node.id.upper()]
            return self.column(node.id)
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _BINARY[type(node.op)](self.visit(node.left), self.visit(node.right))
        if isinstance(node, ast.UnaryOp):
            value = self.visit(node.operand)
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.Not):
                return self.functions["NOT"](value)
        if isinstance(node, ast.Compare):
            left = self.visit(node.left)
            result: Any = True
            for op, comparator in zip(node.ops, node.comparators):
                if type(op) not in _COMPARE:
                    raise FormulaError("Niet-ondersteunde vergelijking.")
                right = self.visit(comparator)
                result = result & _COMPARE[type(op)](left, right)
                left = right
            return result
        if isinstance(node, ast.BoolOp):
            name = "AND" if isinstance(node.op, ast.And) else "OR"
            return self.functions[name](*[self.visit(value) for value in node.values])
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise FormulaError("Ongeldige functie-aanroep.")
            name = node.func.id.upper()
            arguments = [self.visit(argument) for argument in node.args]
            macro = self.macros.get(name)
            if macro is not None:
                if macro["params"] is None:
                    raise FormulaError(f"'{macro['name']}' is een constante, geen functie.")
                return self._call_macro(macro, arguments)
            name = ALIASES.get(name, name)
            function = self.functions.get(name)
            if function is None:
                raise FormulaError(f"Onbekende functie '{node.func.id}'.")
            try:
                return function(*arguments)
            except TypeError as exc:
                raise FormulaError(f"Onjuiste argumenten voor {name}.") from exc
        raise FormulaError("Niet-ondersteunde syntax in formule.")


def evaluate_formula(
    expression: str,
    data: pd.DataFrame,
    header_rows: int,
    macros: dict[str, dict[str, Any]] | None = None,
) -> pd.Series:
    """Berekent de formule voor de meetrijen (na de koprijen) van data."""
    text, references = _preprocess(expression)
    if not text:
        raise FormulaError("Formule is leeg.")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise FormulaError("Syntaxfout in formule.") from exc
    evaluator = _Evaluator(data, header_rows, references, macros)
    try:
        with np.errstate(all="ignore"):
            value = evaluator.visit(tree)
            result = evaluator.series(value).astype(float)
    except FormulaError:
        raise
    except Exception as exc:
        raise FormulaError(str(exc)) from exc
    return result.replace([np.inf, -np.inf], np.nan)


def apply_formulas(
    data: pd.DataFrame,
    formulas: list[dict[str, str]],
    header_rows: int,
    macros: dict[str, dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Geeft (data met extra kolommen, namen van nieuwe kolommen, foutmeldingen)."""
    result = data.copy()
    result.attrs = dict(data.attrs)
    names: list[str] = []
    errors: list[str] = []
    for formula in formulas:
        name = formula["name"].strip()
        try:
            if not name:
                raise FormulaError("Naam ontbreekt.")
            if name.upper() in {str(column).strip().upper() for column in result.columns}:
                raise FormulaError("Er bestaat al een kolom met deze naam.")
            values = evaluate_formula(formula["expression"], result, header_rows, macros)
        except FormulaError as exc:
            errors.append(f"{name or '?'}: {exc}")
            continue
        if header_rows:
            column = pd.Series(index=result.index, dtype=object)
            column.iloc[: header_rows] = [""] * (header_rows - 1) + ["Calc"]
            column.iloc[header_rows:] = values.to_numpy()
        else:
            column = values
        result[name] = column
        names.append(name)
    return result, names, errors
