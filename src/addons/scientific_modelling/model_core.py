from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd


@dataclass
class ModelSpec:
    """Een toepasbaar model: berekent een reeks uit een frame met meetvariabelen."""

    name: str
    kind: str
    inputs: list[str]
    predict: Callable[[pd.DataFrame], pd.Series]
    target: str | None = None
    params: dict[str, float] = field(default_factory=dict)
    errors: dict[str, float] = field(default_factory=dict)
    expression: str = ""
    unit: str = ""
    train_end: pd.Timestamp | None = None
    definition: dict = field(default_factory=dict)
