@echo off
setlocal enabledelayedexpansion
title RaccNet — First-time Setup
cd /d "%~dp0"

echo ============================================
echo           RaccNet Setup
echo ============================================
echo.

REM ── Check for Python ──────────────────────────────────────────────────────────
set PYTHON_OK=0

where py >nul 2>&1
if %errorlevel% == 0 ( set PYTHON_OK=1 )

if !PYTHON_OK! == 0 (
    where python >nul 2>&1
    if !errorlevel! == 0 ( set PYTHON_OK=1 )
)

if !PYTHON_OK! == 1 (
    echo [OK] Python is already installed.
    goto :shortcut
)

echo Python not found.  Downloading Python 3.12 ...
echo (This is a one-time step — about 25 MB)
echo.

powershell -NoProfile -Command ^
  "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe' -OutFile '%TEMP%\py_setup.exe' -UseBasicParsing"

if errorlevel 1 (
    echo.
    echo ERROR: Download failed.  Please install Python manually:
    echo   https://www.python.org/downloads/
    echo Then re-run this installer.
    pause
    exit /b 1
)

echo Installing Python silently ...
"%TEMP%\py_setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
del "%TEMP%\py_setup.exe"

REM Refresh PATH for this session
for /f "delims=" %%i in ('powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable(\"PATH\",\"User\")"') do set "PATH=%%i;%PATH%"

echo [OK] Python installed.
echo.

:shortcut
REM ── Create Desktop shortcut (a .bat file) ─────────────────────────────────────
set "DEST=%USERPROFILE%\Desktop\RaccNet.bat"

(
    echo @echo off
    echo title RaccNet
    echo cd /d "%~dp0"
    echo where py ^>nul 2^>^&1
    echo if %%errorlevel%% == 0 ^( py "%~dp0launcher.py" ^) else ^( python "%~dp0launcher.py" ^)
    echo pause
) > "%DEST%"

echo [OK] Shortcut created: %DEST%
echo.
echo ============================================
echo  Setup complete!
echo  Double-click "RaccNet" on your Desktop to launch.
echo  On first launch it will download FFmpeg (~80 MB)
echo  for video upload support.
echo ============================================
echo.
pause
