@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 安装打包依赖...
python -m pip install -q pyinstaller pywebview pymupdf pymupdf4llm
echo 开始打包...
python -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name "PDF翻译专家" --icon "docs/app.ico" ^
  --hidden-import webview.platforms.edgechromium --hidden-import clr ^
  --exclude-module cv2 --exclude-module librosa --exclude-module torch ^
  --exclude-module matplotlib --exclude-module scipy --exclude-module pandas ^
  --exclude-module onnxruntime --exclude-module sklearn --exclude-module tkinter ^
  --collect-submodules webview pdf_translator_desktop.py
echo.
echo 完成： dist\PDF翻译专家.exe
pause
