@echo off
setlocal
title KANSLOR Planes

cd /d "%~dp0"

echo ==========================================
echo            KANSLOR PLANES
echo ==========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3 was not found.
    echo Install Python 3 and enable "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

python -c "import curses" >nul 2>&1
if errorlevel 1 (
    echo First-time setup: installing Windows terminal support...
    python -m pip install windows-curses

    if errorlevel 1 (
        echo.
        echo [ERROR] Could not install windows-curses.
        pause
        exit /b 1
    )
)

echo Updating aircraft data...
python collect.py

if errorlevel 1 (
    echo.
    echo [WARNING] Aircraft update failed.
    echo Planes will attempt to open using existing local data.
    echo.
)

echo Starting Planes...
echo.

python aircraft_ui.py

if errorlevel 1 (
    echo.
    echo [ERROR] Planes exited with an error.
    pause
)

endlocal