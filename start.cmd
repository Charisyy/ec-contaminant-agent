@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing_environment
if not exist "data\catalog.sqlite" goto missing_database
".venv\Scripts\python.exe" -B launch.py
if errorlevel 1 pause
exit /b
:missing_environment
echo 请先双击 setup.cmd 安装环境。具体步骤见 README.md。
pause
exit /b 1
:missing_database
echo 缺少 data\catalog.sqlite。请完整解压交付包，不要只复制网页文件。
echo 此启动入口不会自动重建数据库。
pause
exit /b 1
