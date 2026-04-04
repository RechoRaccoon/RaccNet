@echo off
title Build RaccNet.exe
cd /d "%~dp0"

echo ============================================
echo       Building RaccNet.exe
echo ============================================
echo.
echo This packages RaccNet into a single .exe that
echo users can run without installing Python.
echo.

REM Install PyInstaller if needed
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM Build the exe
REM   --onefile        = single .exe (slower start, easier to share)
REM   --console        = keep console window so users see status output
REM   --name RaccNet  = output filename
REM   --icon           = app icon (requires Pillow: pip install pillow)
echo.
echo Building...

pyinstaller ^
  --onefile ^
  --console ^
  --name RaccNet ^
  --icon "RaccNet Icon.ico" ^
  --add-data "raccnet_server.py;." ^
  --add-data "RaccNet Icon.ico;." ^
  launcher.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED. See errors above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Done!  Output: dist\RaccNet.exe
echo  Upload that file to GitHub Releases so
echo  users can download it directly.
echo ============================================
echo.
pause
