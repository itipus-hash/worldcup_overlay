# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the WorldCup 2026 desktop overlay.
# Run with: pyinstaller WorldCupOverlay.spec

import os
import sys
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Collect Qt platform plugins (the .dylib files) and the data files
# PyQt5 needs at runtime.
datas = []
binaries = []

# We need to copy the "gear.png" asset next to the bundle so the icon
# loaders can find it (if any).
datas += [('gear.icns', '.')]

# PyQt5's QtQml / QtQuick may pull in qml dirs, but we don't use QML
# here — leave them out to keep the bundle small.

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'PyQt5',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim fat: these aren't used by the app and bloat the bundle.
        'tkinter', 'unittest', 'pydoc', 'doctest',
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'PIL', 'cv2', 'pytest', 'IPython',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WorldCupOverlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # GUI app — no terminal window on macOS
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='WorldCupOverlay',
)
# macOS .app bundle assembly
app = BUNDLE(
    coll,
    name='WorldCupOverlay.app',
    icon='gear.icns',
    bundle_identifier='com.worldcup.overlay',
    info_plist={
        'CFBundleName': 'WorldCupOverlay',
        'CFBundleDisplayName': '2026 世界杯悬浮窗',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1',
        'LSMinimumSystemVersion': '10.13',
        'NSHighResolutionCapable': True,
        # Hide from Dock — this is a menu-bar / floating overlay app.
        'LSUIElement': False,
        # Allow network egress to ESPN / pushplus.
        'NSAppTransportSecurity': {
            'NSAllowsArbitraryLoads': True,
        },
    },
)
