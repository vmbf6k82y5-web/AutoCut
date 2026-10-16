#!/usr/bin/env bash
# AutoCut PyInstaller 打包脚本（macOS/Linux 调试用）
set -e
uv python install 3.11 2>/dev/null || true
uv venv --python 3.11 .venv
uv pip install -e ".[dev]" pyinstaller
rm -rf build dist
.venv/bin/pyinstaller autocut.spec --noconfirm
echo "产物: dist/autocut/autocut"
