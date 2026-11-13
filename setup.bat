@echo off
REM 1-Click PocketLLM launcher for Windows Command Prompt.
setlocal
cd /d "%~dp0"

py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if %errorlevel%==0 (
    py -3 "%~dp0run.py" %*
    exit /b %errorlevel%
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if %errorlevel%==0 (
    python "%~dp0run.py" %*
    exit /b %errorlevel%
)

echo Error: Python 3.8+ is required.
echo Install via winget:
echo   winget install -e --id Python.Python.3.12
exit /b 1
