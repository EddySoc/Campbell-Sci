from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import Tuple

import pandas as pd


def parse_filename(filename: str) -> dict:
    """Parse filenames like BRAS_2025_08_22_10_30.dat or
    WX_EEN_KwartierW_2011_01_14_16_01_48.dat.

    The logger name is everything before the trailing timestamp fragment.
    The timestamp is not the routing key; it is only used to distinguish files
    from the same logger at different moments in time.

    Returns a dictionary with:
    - logger: BRAS, TDR, WX_EEN_KWARTIERW, BM, etc.
    - date_string: 2025_08_22_10_30 or 2011_01_14_16_01_48
    - stem: original filename without extension
    - date_token: alias for date_string
    """
    stem = Path(filename).stem
    match = re.search(
        r"(?P<date>\d{4}_\d{2}_\d{2}(?:_\d{2}(?:_\d{2}(?:_\d{2})?)?)?)$",
        stem,
    )

    if not match:
        raise ValueError(f"Bestandsnaam voldoet niet aan het patroon: {filename}")

    date_string = match.group("date")
    logger = stem[: match.start()].rstrip("_")
    if not logger:
        raise ValueError(f"Kon geen logger-prefix vinden in: {filename}")

    return {
        "logger": logger.upper(),
        "date_string": date_string,
        "date_token": date_string,
        "stem": stem,
    }


def read_dat_file(file_path: str | Path) -> pd.DataFrame:
    """Read Campbell TOA5-style .dat files.

    We keep the full Campbell structure: header row, info/unit row, and all
    timestamped measurement rows. The fallback workbook can then write the raw
    data to the Data sheet exactly as exported by the logger.
    """
    path = Path(file_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    header_index = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith('"TIMESTAMP"') or upper.startswith('TIMESTAMP'):
            header_index = idx
            break

    if header_index is None:
        raise ValueError(f"Geen Campbell TOA5 header gevonden in {path}")

    csv_text = "\n".join(lines[header_index:])
    df = pd.read_csv(
        StringIO(csv_text),
        sep=',',
        quotechar='"',
        engine='python',
        on_bad_lines='skip',
        header=0,
    )

    if df.empty:
        raise ValueError(f"Geen data gevonden in bestand: {path}")

    return df


def detect_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, list[str]]:
    """Select the timestamp column and the first real measurement column for one chart."""
    if df.shape[1] < 2:
        raise ValueError("Bestand bevat te weinig kolommen voor grafiekverwerking.")

    time_col = df.columns[0]
    value_col = None
    for col in df.columns[1:]:
        name = str(col).strip().upper()
        if name not in {"RECORD", "RN", ""}:
            value_col = col
            break

    if value_col is None:
        value_col = df.columns[1]

    result = df[[time_col, value_col]].copy()
    result.columns = ["time", "value"]

    # Strip leftover metadata rows just in case.
    result = result[result["time"].astype(str).str.match(r"^\d{4}-\d{2}-\d{2}", na=False)].reset_index(drop=True)
    return result, ["time", "value"]
