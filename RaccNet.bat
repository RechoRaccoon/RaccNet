@echo off
title RaccNet
cd /d "%~dp0"

REM Try "py" launcher first (standard Windows Python installer), then "python"
where py >nul 2>&1
if %errorlevel% == 0 (
    py launcher.py
) else (
    python launcher.py
)

REM If we get here the server stopped — keep window open so user can read any error
pause
