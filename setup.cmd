@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0"
if /i "%~1"=="--check" goto find_python
if exist ".venv\Scripts\python.exe" goto install
:find_python
set "PROJECT_PYTHON="
py -3.12 -c "import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8" >nul 2>&1
if errorlevel 1 goto try_python
set "PROJECT_PYTHON=py -3.12"
goto found_python
:try_python
python -c "import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8" >nul 2>&1
if errorlevel 1 goto try_local_runtime
set "PROJECT_PYTHON=python"
goto found_python
:try_local_runtime
set "LOCAL_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%LOCAL_PYTHON%" goto missing_python
"%LOCAL_PYTHON%" -c "import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8" >nul 2>&1
if errorlevel 1 goto missing_python
set PROJECT_PYTHON="%LOCAL_PYTHON%"
:found_python
echo 已找到本项目需要的 Python 3.12（64位）：
%PROJECT_PYTHON% -c "import sys; print(sys.executable)"
if /i "%~1"=="--check" exit /b 0
%PROJECT_PYTHON% -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -c "import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8" >nul 2>&1
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
echo 未找到本项目需要的 Python 3.12（64位）。已有其他版本不代表没有安装 Python。
echo 以下是 Python 启动器识别的版本：
py -0p 2>nul
echo 本项目尚未验证 Python 3.14；不要卸载原有版本，可另外安装 3.12。
echo 请按 README.md 安装对应版本，再重新运行。
if /i "%~1"=="--check" exit /b 1
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
