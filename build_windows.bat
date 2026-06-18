@echo off
setlocal

cd /d "%~dp0"

echo Installing build dependency...
python -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

echo Building portable EXE...
python -m PyInstaller --clean --noconfirm IGESCutCalculator.spec
if errorlevel 1 exit /b 1

echo.
echo Done.
echo Portable EXE: %cd%\dist\IGESCutCalculator.exe
echo.
echo To build an installer, install Inno Setup and compile installer\IGESCutCalculator.iss
