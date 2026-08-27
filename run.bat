@echo off
rem  FW - a worldbuilding application for fiction writers.
rem  Double-click this file to start it. The first run sets everything up;
rem  after that it goes straight to the app.
setlocal EnableExtensions
cd /d "%~dp0"

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    echo Python 3.11 or newer is needed and was not found.
    echo.
    echo Install it from https://www.python.org/downloads/
    echo   ^(tick "Add python.exe to PATH" in the installer^)
    echo then double-click this file again.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up a private Python environment...
    %PY% -m venv .venv || goto :setup_failed
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    echo Installing the application - this needs the internet once...
    ".venv\Scripts\python.exe" -m pip install -e . --quiet || goto :setup_failed
    echo Done. This part never runs again.
)

echo Starting FW... the app will open in your browser.
echo Leave this window open while you work; close it to stop the app.
".venv\Scripts\python.exe" -m fw serve --open
pause
exit /b 0

:setup_failed
echo.
echo Setup did not finish - the messages above say why.
echo ^(Most often: no internet on the first run, or an old Python.^)
pause
exit /b 1
