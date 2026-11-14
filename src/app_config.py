from __future__ import annotations

from pathlib import Path
import shutil
import sys

from i18n import t

_APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CONFIGS_DIR = _APP_DIR / "Configs"


def prepare_external_configs() -> None:
    if not getattr(sys, "frozen", False):
        return

    bundled_configs = Path(__file__).with_name("Configs")
    if not bundled_configs.is_dir():
        if CONFIGS_DIR.is_dir():
            return
        raise FileNotFoundError(t("err.bundled_configs_missing", path=bundled_configs))

    if bundled_configs.resolve() == CONFIGS_DIR.resolve():
        return

    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    for source in bundled_configs.rglob("*"):
        destination = CONFIGS_DIR / source.relative_to(bundled_configs)
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
