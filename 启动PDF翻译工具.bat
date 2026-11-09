@echo off
cd /d "%~dp0"
if exist "PDF翻译专家.exe" (
  start "" "PDF翻译专家.exe"
  exit /b 0
)
if exist "启动PDF翻译工具.vbs" (
  start "" "启动PDF翻译工具.vbs"
  exit /b 0
)
echo 未找到 PDF翻译专家.exe 或 启动PDF翻译工具.vbs，请确认文件完整。
pause
