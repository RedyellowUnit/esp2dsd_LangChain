# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all

datas = collect_data_files("tiktoken")
datas += collect_data_files("tiktoken_ext")

binaries = []
hiddenimports = []
hiddenimports += collect_submodules("tiktoken_ext")

# py7zz: RAR 展開用。bin/7zz.exe と 7z.dll を必ず同梱する
try:
    py7zz_datas, py7zz_binaries, py7zz_hidden = collect_all("py7zz")
    datas += py7zz_datas
    binaries += py7zz_binaries
    hiddenimports += py7zz_hidden
except Exception:
    pass

# collect_all で漏れた場合の明示同梱
try:
    import py7zz

    py7zz_bin_dir = Path(py7zz.__file__).resolve().parent / "bin"
    for name in ("7zz.exe", "7z.dll", "7zz", "7zz.so"):
        candidate = py7zz_bin_dir / name
        if candidate.exists():
            binaries.append((str(candidate), "py7zz/bin"))
except Exception:
    pass

# py7zr（7z 展開）
try:
    datas += collect_data_files("py7zr")
    hiddenimports += collect_submodules("py7zr")
except Exception:
    pass

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='translate_plugin2dsd',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['7zz.exe', '7z.dll', '7zz', '7z.dll'],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
