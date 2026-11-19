@echo off
rem Zadanie APRSBox-Watchdog da sie usunac tylko jako administrator - bez tego ginie sam proces, a zadanie podnosi watchdoga w ciagu 5 min. Dlatego skrypt sam prosi o zgode (UAC).
net session >nul 2>&1 || (powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-watchdog.ps1"
if errorlevel 1 (pause) else (timeout /t 4 /nobreak >nul)
