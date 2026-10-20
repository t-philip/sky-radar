@echo off
setlocal

set "VENV_DIR=%LOCALAPPDATA%\venvs\sky_radar"
set "APP_DIR=%~dp0"

echo.
echo  Sky Radar Launcher
echo  ------------------
echo.

:: Locate Python - try py launcher, then PATH python, then common user-install paths
set "PYTHON_CMD="

where py >nul 2>&1
if %errorlevel% equ 0 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python >nul 2>&1
    if %errorlevel% equ 0 (
        python --version >nul 2>&1
        if %errorlevel% equ 0 set "PYTHON_CMD=python"
    )
)

if not defined PYTHON_CMD (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" set "PYTHON_CMD=%%D\python.exe"
    )
)

if not defined PYTHON_CMD (
    echo ERROR: Python not found.
    echo Install Python 3.10+ from https://www.python.org/ and try again.
    pause
    exit /b 1
)

:: Create venv if it does not exist on this machine
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating virtual environment at:
    echo   %VENV_DIR%
    echo.
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Sync dependencies - fast no-op if already up to date
echo Checking dependencies...
"%VENV_DIR%\Scripts\pip.exe" install -q -r "%APP_DIR%requirements.txt"
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

:: Launch the app
echo.
echo  Open http://localhost:5000 in your browser.
echo  Press Ctrl+C to stop.
echo.
"%VENV_DIR%\Scripts\python.exe" "%APP_DIR%app.py"

pause