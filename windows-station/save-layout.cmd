@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0save-layout.ps1"
if errorlevel 1 (pause) else (timeout /t 4 /nobreak >nul)
