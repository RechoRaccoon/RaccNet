$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host ""
Write-Host " =============================="
Write-Host "   RaccNet Android APK Builder"
Write-Host " =============================="
Write-Host ""
Write-Host " Builds/updates the Android APK using GitHub Actions (free cloud build)."
Write-Host " Run this any time you update raccnet_server.py -- the build copies it"
Write-Host " into the APK automatically. No manual file copying needed."
Write-Host ""
Write-Host " Build times: ~25 min first run, ~8 min after that (SDK is cached)."
Write-Host ""

# Check git is installed
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host " ERROR: git is not installed." -ForegroundColor Red
    Write-Host " Download it from: https://git-scm.com/download/win"
    Read-Host " Press Enter to exit"
    exit 1
}

# Get remote URL
$remoteUrl = git remote get-url origin 2>$null
if (-not $remoteUrl) {
    Write-Host " ERROR: This project is not connected to a GitHub repo yet." -ForegroundColor Red
    Write-Host ""
    Write-Host " One-time setup:"
    Write-Host "   1. Go to https://github.com/new and create a NEW repository"
    Write-Host "   2. Copy the repo URL (e.g. https://github.com/YourName/raccnet.git)"
    Write-Host "   3. Run these commands in this folder:"
    Write-Host "        git init"
    Write-Host "        git remote add origin https://github.com/YourName/raccnet.git"
    Write-Host "        git add ."
    Write-Host '        git commit -m "Initial commit"'
    Write-Host "        git push -u origin main"
    Write-Host "   4. Then run this file again."
    Read-Host " Press Enter to exit"
    exit 1
}

$actionsUrl = $remoteUrl.TrimEnd('/') -replace '\.git$', ''
$actionsUrl = "$actionsUrl/actions"

Write-Host " [1/3] Staging all changed files..."
git add -A
if ($LASTEXITCODE -ne 0) { Write-Host " ERROR: git add failed." -ForegroundColor Red; Read-Host; exit 1 }

$staged = git diff --cached --quiet 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "       No changes since last build -- pushing anyway to trigger a build."
    git commit --allow-empty -m "Trigger Android APK rebuild" | Out-Null
} else {
    Write-Host " [2/3] Committing changes..."
    git commit -m "Update RaccNet - rebuild APK"
    if ($LASTEXITCODE -ne 0) { Write-Host " ERROR: git commit failed." -ForegroundColor Red; Read-Host; exit 1 }
}

Write-Host " [3/3] Pushing to GitHub..."
git push
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host " ERROR: git push failed. Possible reasons:" -ForegroundColor Red
    Write-Host "   - Not logged in to GitHub"
    Write-Host "   - No internet connection"
    Read-Host " Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host " Pushed! GitHub is now building your APK." -ForegroundColor Green
Write-Host ""
Write-Host " Watch the build at: $actionsUrl"
Write-Host ""
Write-Host " When it finishes (green checkmark):"
Write-Host "   1. Click the completed build run"
Write-Host "   2. Scroll to 'Artifacts' at the bottom"
Write-Host "   3. Download 'RaccNet-Android-APK'"
Write-Host "   4. Unzip it -- the .apk is inside"
Write-Host "   5. Send the .apk to whoever needs it (Discord, Google Drive, etc.)"
Write-Host "   6. On their phone: tap the .apk to install"
Write-Host "      (Allow 'Install unknown apps' if prompted -- this is normal)"
Write-Host "   7. If they have an older version, Android upgrades it automatically."
Write-Host ""
Start-Process $actionsUrl
Read-Host " Press Enter to close"
