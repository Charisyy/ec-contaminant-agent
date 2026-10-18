@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
py -3.12 -c "import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8" >nul 2>&1
if errorlevel 1 goto try_python
py -3.12 -m venv .venv
if errorlevel 1 goto failed
goto install
:try_python
python -c "import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8" >nul 2>&1
if errorlevel 1 goto missing_python
python -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -c "import sys; assert sys.version_info[:2]==(3,12)" >nul 2>&1
if errorlevel 1 goto wrong_environment
echo 正在安装依赖，首次安装需要联网，请稍候。
".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -e . --no-deps
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -B check_environment.py
if errorlevel 1 goto failed
echo 安装完成。以后双击 start.cmd 即可使用，无需重复安装。
pause
exit /b 0
:missing_python
echo 未找到 Python 3.12（64位）。请先按“师兄使用说明.md”安装，再重新运行。
pause
exit /b 1
:wrong_environment
echo 现有 .venv 不是 Python 3.12。请先备份并另存此环境，然后重新安装。
pause
exit /b 1
:failed
echo 安装未完成，请保留此窗口的错误信息以便排查。数据库没有被重建。
pause
exit /b 1
