@echo off
setlocal
title 安装腾讯免费翻译引擎
cd /d "%~dp0"

set "HOME_DIR=%PDF2ZH_HOME%"
if defined HOME_DIR if not exist "%HOME_DIR%\env\python.exe" set "HOME_DIR="
if not defined HOME_DIR if exist "D:\PDF_Translator\env\python.exe" set "HOME_DIR=D:\PDF_Translator"
if not defined HOME_DIR goto nohome

echo [1/2] 运行环境: %HOME_DIR%
echo [2/2] 正在安装腾讯免费翻译引擎...
echo.
"%HOME_DIR%\env\python.exe" "%~dp0install_tencent_free.py"
if errorlevel 1 goto failed
echo.
echo 安装完成。界面里可选「BabelDOC + 腾讯」，命令行可用:
echo    pdf2zh_next 输入.pdf --tencentfree --lang-in en --lang-out zh
echo.
pause
exit /b 0

:nohome
echo 未找到 BabelDOC 运行环境（pdf2zh_next）。
echo.
echo 提示: 不装 BabelDOC 也能用本软件，只是只能用「本地引擎」。
echo 想用更好的排版，请先安装 pdf2zh_next，再设置环境变量 PDF2ZH_HOME。
echo.
pause
exit /b 1

:failed
echo.
echo 安装失败，请查看上面的错误信息。
pause
exit /b 1
