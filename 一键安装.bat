@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing dependencies... 正在安装依赖(约1-3分钟)...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 pip install -r requirements.txt
echo.
echo Done! 安装完成! 现在可双击 ScanPathViewer演示.bat
pause
