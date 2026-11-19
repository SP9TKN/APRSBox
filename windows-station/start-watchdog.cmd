@echo off
rem Rejestracja zadania APRSBox-Watchdog wymaga administratora - skrypt sam prosi o zgode (UAC).
net session >nul 2>&1 || (powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-watchdog.ps1"
if errorlevel 1 (pause) else (timeout /t 4 /nobreak >nul)
