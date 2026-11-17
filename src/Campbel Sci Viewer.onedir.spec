# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import shutil
from PyInstaller.utils.hooks import collect_submodules

project_dir = Path(SPECPATH)
icon_path = project_dir.parent / 'screenshots' / 'CampBell Icon.ico'
hidden = collect_submodules('addons')

a = Analysis(
    [str(project_dir / 'main.py')],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[(str(icon_path), '.')],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'tensorboard', 'dask', 'jax', 'tensorflow', 'IPython', 'pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Campbel Sci Viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Campbel Sci Viewer',
)

config_source = project_dir / 'Configs'
config_target = Path(coll.name) / 'Configs'
if not config_source.is_dir():
    raise FileNotFoundError(f'Configuration directory not found: {config_source}')
for source in config_source.rglob('*'):
    destination = config_target / source.relative_to(config_source)
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
    elif source.parent.name in {'Langdefs', 'Helpdefs'} or not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
