@echo off
chcp 65001 >nul
title 安装腾讯免费翻译引擎
cd /d "%~dp0"

set "HOME_DIR=%PDF2ZH_HOME%"
if "%HOME_DIR%"=="" if exist "D:\PDF_Translator\env\python.exe" set "HOME_DIR=D:\PDF_Translator"
if "%HOME_DIR%"=="" (
  echo 未找到 BabelDOC 运行环境。
  echo 请先安装 pdf2zh_next，或设置环境变量 PDF2ZH_HOME 指向安装目录。
  echo.
  echo 提示：不装也能用本软件，只是只有「本地引擎」可用。
  pause
  exit /b 1
)

echo 使用环境：%HOME_DIR%
"%HOME_DIR%\env\python.exe" "install_tencent_free.py"
echo.
echo 完成。命令行可用：pdf2zh_next 输入.pdf --tencentfree --lang-in en --lang-out zh
pause
