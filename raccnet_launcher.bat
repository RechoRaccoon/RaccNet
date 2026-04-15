@echo off
title RaccNet Launcher
cd /d "%~dp0"

:start
echo.
echo  ============================
echo   Starting RaccNet...
echo  ============================
echo.
python raccnet_server.py
echo.
echo  RaccNet stopped. Restarting in 3 seconds...
echo  (Close this window to exit)
echo.
timeout /t 3 /nobreak >nul
goto start
