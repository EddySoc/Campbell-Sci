from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import app_config


class PrepareExternalConfigsTests(unittest.TestCase):
    def test_onedir_uses_configs_next_to_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configs = root / "Configs"
            configs.mkdir()
            (configs / "existing.langdef").write_text("unchanged", encoding="utf-8")
            with patch.object(app_config.sys, "frozen", True, create=True), \
                 patch.object(app_config, "__file__", str(root / "_internal" / "app_config.py")), \
                 patch.object(app_config, "CONFIGS_DIR", configs):
                app_config.prepare_external_configs()
            self.assertEqual((configs / "existing.langdef").read_text(encoding="utf-8"), "unchanged")

    def test_onefile_copies_missing_files_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / "bundle" / "Configs"
            bundled.mkdir(parents=True)
            (bundled / "existing.txt").write_text("default", encoding="utf-8")
            (bundled / "new.txt").write_text("new default", encoding="utf-8")
            configs = root / "app" / "Configs"
            configs.mkdir(parents=True)
            (configs / "existing.txt").write_text("user", encoding="utf-8")
            with patch.object(app_config.sys, "frozen", True, create=True), \
                 patch.object(app_config, "__file__", str(bundled.parent / "app_config.py")), \
                 patch.object(app_config, "CONFIGS_DIR", configs):
                app_config.prepare_external_configs()
            self.assertEqual((configs / "existing.txt").read_text(encoding="utf-8"), "user")
            self.assertEqual((configs / "new.txt").read_text(encoding="utf-8"), "new default")

    def test_missing_bundled_and_external_configs_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(app_config.sys, "frozen", True, create=True), \
                 patch.object(app_config, "__file__", str(root / "_internal" / "app_config.py")), \
                 patch.object(app_config, "CONFIGS_DIR", root / "Configs"):
                with self.assertRaises(FileNotFoundError):
                    app_config.prepare_external_configs()


if __name__ == "__main__":
    unittest.main()
