@echo off
rem Start the APRSBox window (no console window).
rem Uses the project virtual environment (.venv) or the Python bundled with the portable ZIP (python\).
setlocal
cd /d "%~dp0"
set "APRSBOX_PYTHONW=.venv\Scripts\pythonw.exe"
if not exist "%APRSBOX_PYTHONW%" set "APRSBOX_PYTHONW=python\pythonw.exe"
if not exist "%APRSBOX_PYTHONW%" (
    echo Brak Pythona: nie ma ani .venv, ani folderu python - zobacz README-WINDOWS.md
    pause
    exit /b 1
)
start "" "%APRSBOX_PYTHONW%" "%~dp0APRSBox.pyw" %*
