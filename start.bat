@echo off
setlocal
cd /d "%~dp0"
echo === StepAhead Lab 1.1.2 -- PAPER ONLY ===
where py >nul 2>&1
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -e .
if errorlevel 1 goto :fail
.venv\Scripts\python.exe -m unittest discover -s tests -q
if errorlevel 1 goto :fail
echo Dashboard: http://127.0.0.1:8765/
echo Venue clock will synchronize against public Polymarket /time endpoint.
echo Press Ctrl+C to stop. There are NO live orders.
.venv\Scripts\stepahead.exe start
pause
exit /b 0
:fail
echo Setup failed. Check Python 3.11+, pip, and internet access.
pause
exit /b 1
