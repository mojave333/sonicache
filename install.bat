@echo off
rem Creates .venv next to this file and installs or updates dependencies.
rem Run it again from time to time: YouTube changes often and yt-dlp follows.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo Python 3.10+ not found. Install it from https://www.python.org/downloads/
    if not "%~1"=="--quiet" pause
    exit /b 1
)

echo Installing / updating dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
    echo Dependency installation failed.
    if not "%~1"=="--quiet" pause
    exit /b 1
)

echo Done.
if not "%~1"=="--quiet" pause
