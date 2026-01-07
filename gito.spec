# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['gito/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('gito/config.toml', 'gito'),
        ('gito/tpl', 'gito/tpl'),
    ],
    hiddenimports=['tiktoken_ext.openai_public', 'tiktoken_ext'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='gito',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='press-kit/logo/gito-ai-code-reviewer_logo.ico',  # Uncomment when .ico file is available
)
