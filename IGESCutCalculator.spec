# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all


try:
    ocp_datas, ocp_binaries, ocp_hiddenimports = collect_all("OCP")
except Exception:
    ocp_datas, ocp_binaries, ocp_hiddenimports = [], [], []


a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=ocp_binaries,
    datas=ocp_datas,
    hiddenimports=ocp_hiddenimports,
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
    name="IGESCutCalculator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
