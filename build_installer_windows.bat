@echo off
setlocal

cd /d "%~dp0"

call build_windows.bat
if errorlevel 1 exit /b 1

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if not exist "%ISCC%" (
    echo Inno Setup 6 was not found.
    echo Download and install it from https://jrsoftware.org/isinfo.php
    exit /b 1
)

echo Building installer...
"%ISCC%" installer\IGESCutCalculator.iss
if errorlevel 1 exit /b 1

echo.
echo Done.
echo Installer: %cd%\dist\IGESCutCalculatorSetup.exe
