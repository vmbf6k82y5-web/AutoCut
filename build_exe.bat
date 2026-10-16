@echo off
REM AutoCut PyInstaller 打包脚本（Windows）
REM 用法: 双击或命令行运行 build_exe.bat
REM 产物: dist\autocut\autocut.exe（单目录）

echo [1/4] 安装 uv 与依赖...
where uv >nul 2>nul || (powershell -c "irm https://astral.sh/uv/install.ps1 | iex")
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install -e ".[dev]" pyinstaller --python .venv\Scripts\python.exe

echo [2/4] 清理旧产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/4] PyInstaller 打包...
.venv\Scripts\pyinstaller.exe autocut.spec --noconfirm

echo [4/4] 完成。产物在 dist\autocut\
echo 双击 dist\autocut\autocut.exe 打开 GUI；命令行: autocut.exe run ^<视频^>
pause
