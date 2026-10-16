# PyInstaller 单目录打包 spec。ffmpeg 不打包进 exe。
# 用法: pyinstaller autocut.spec

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

datas = [
    ("src/autocut/prompts", "autocut/prompts"),
    ("src/autocut/web/static", "autocut/web/static"),
    ("config.example.toml", "."),
    ("skills", "skills"),
]

hiddenimports = (
    collect_submodules("autocut")
    + collect_submodules("edge_tts")
    + collect_submodules("scenedetect")
    + ["cv2", "openai", "fastapi", "uvicorn", "mcp", "pydantic", "typer"]
)

a = Analysis(
    ["src/autocut/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "tensorflow", "matplotlib"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="autocut",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 保留控制台以便看日志；GUI 模式仍开浏览器
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="autocut",
)
