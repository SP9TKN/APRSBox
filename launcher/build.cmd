@echo off
rem Builds APRSBox.exe (the launcher with the APRSBox icon) with the C# compiler that ships with
rem Windows (.NET Framework 4.x). The icon comes from launcher\APRSBox.ico (make_icon.py).
setlocal
cd /d "%~dp0"
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe

"%CSC%" /nologo /codepage:65001 /target:winexe /platform:anycpu /optimize+ /out:..\APRSBox.exe ^
  /win32icon:APRSBox.ico /r:System.dll /r:System.Windows.Forms.dll APRSBox.cs || goto :error
echo OK: APRSBox.exe
exit /b 0

:error
echo BUILD FAILED
exit /b 1
