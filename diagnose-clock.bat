@echo off
setlocal
cd /d "%~dp0"
echo === StepAhead venue clock / Windows clock diagnostic ===
if not exist ".venv\Scripts\python.exe" (
    echo Local Python environment missing. Run start.bat once to install it.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m stepahead_lab clock
if errorlevel 1 (
    echo Exchange time calibration failed: do not trust quote scans until resolved.
    pause
    exit /b 1
)
echo.
echo === Windows Time service ===
w32tm /query /status
if errorlevel 1 echo Windows Time not available or user permission insufficient.
echo.
echo If venue-minus-Windows offset exceeds 5s, open Windows Settings ^> Time ^& language ^> Date ^& time ^> Sync now.
echo Don't disable stale-book safeguards.
pause
