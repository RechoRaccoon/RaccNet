@echo off
title RaccNet - Build Android APK
echo.
echo  ==============================
echo   RaccNet Android APK Builder
echo  ==============================
echo.
echo  This builds the APK using GitHub Actions (free cloud build).
echo  It takes ~25 min on first run, ~8 min after that.
echo.

REM ── Check git is installed ─────────────────────────────────────────────────
where git >nul 2>nul
if errorlevel 1 (
    echo  ERROR: git is not installed.
    echo  Download it from: https://git-scm.com/download/win
    echo  Then re-run this file.
    pause
    exit /b 1
)

REM ── Get the GitHub repo URL from git remote ────────────────────────────────
cd /d "%~dp0"
for /f "tokens=*" %%i in ('git remote get-url origin 2^>nul') do set "REMOTE_URL=%%i"

if "%REMOTE_URL%"=="" (
    echo  ERROR: This project is not connected to a GitHub repo yet.
    echo.
    echo  One-time setup:
    echo    1. Go to https://github.com/new and create a NEW repository
    echo       Name it: raccnet  (or anything you like)
    echo       Set it to Private if you don't want it public
    echo    2. Copy the repo URL (looks like: https://github.com/YourName/raccnet.git)
    echo    3. Run these commands in this folder:
    echo         git init
    echo         git remote add origin https://github.com/YourName/raccnet.git
    echo         git add .
    echo         git commit -m "Initial commit"
    echo         git push -u origin main
    echo    4. Then run this bat file again.
    echo.
    pause
    exit /b 1
)

REM ── Build the GitHub Actions page URL ─────────────────────────────────────
REM Convert  https://github.com/user/repo.git  to  https://github.com/user/repo/actions
set "ACTIONS_URL=%REMOTE_URL:.git=%"
set "ACTIONS_URL=%ACTIONS_URL%/actions"

echo  [1/3] Staging all changed files...
git add -A
if errorlevel 1 ( echo  ERROR: git add failed. & pause & exit /b 1 )

REM Check if there's actually anything to commit
git diff --cached --quiet 2>nul
if not errorlevel 1 (
    echo         No changes since last build — pushing anyway to trigger a build.
    git commit --allow-empty -m "Trigger Android APK rebuild" >nul 2>nul
) else (
    echo  [2/3] Committing changes...
    git commit -m "Update RaccNet - rebuild APK"
    if errorlevel 1 ( echo  ERROR: git commit failed. & pause & exit /b 1 )
)

echo  [3/3] Pushing to GitHub...
git push
if errorlevel 1 (
    echo.
    echo  ERROR: git push failed. Possible reasons:
    echo    - Not logged in to GitHub: run  git credential-manager  or use GitHub Desktop
    echo    - No internet connection
    pause
    exit /b 1
)

echo.
echo  ✓ Pushed! GitHub is now building your APK.
echo.
echo  Opening GitHub Actions page in your browser...
echo  Watch the build progress there. When it finishes (green checkmark):
echo    1. Click on the build run
echo    2. Scroll to "Artifacts" at the bottom
echo    3. Download "RaccNet-Android-APK"
echo    4. Unzip it — the .apk file is inside
echo    5. Copy it to your phone and install it
echo.
echo  Actions URL: %ACTIONS_URL%
echo.
start "" "%ACTIONS_URL%"

pause
