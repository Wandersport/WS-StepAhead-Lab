@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish-to-github.ps1"
if errorlevel 1 (
    echo GitHub publication did not succeed. See the error above.
    pause
    exit /b 1
)
pause
