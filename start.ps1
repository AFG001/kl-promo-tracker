# KL Electronics Promo Tracker — PowerShell Startup

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  KL Electronics Promo Tracker" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan

# --- Check .env ---
if (-not (Test-Path ".env")) {
    Write-Host "[WARN] .env not found. Copying from .env.example ..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "[ACTION] Open .env and set ANTHROPIC_API_KEY and Firebase vars." -ForegroundColor Red
    Start-Process notepad ".env"
    Read-Host "Press Enter when .env is ready"
}

# --- Check Firebase service account ---
$envContent = Get-Content ".env" -Raw
if ($envContent -match 'FIREBASE_SERVICE_ACCOUNT_KEY=(.+)') {
    $keyPath = $Matches[1].Trim()
    if (-not (Test-Path $keyPath)) {
        Write-Host "[WARN] Firebase service account key not found at: $keyPath" -ForegroundColor Yellow
        Write-Host "       Download it from Firebase Console -> Project Settings -> Service Accounts" -ForegroundColor Yellow
    }
}

# --- Load .env into process environment ---
Get-Content ".env" | ForEach-Object {
    if ($_ -match "^\s*([^#][^=]+)=(.+)$") {
        [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
    }
}

# --- Install dependencies ---
Write-Host "[INFO] Installing Python dependencies ..." -ForegroundColor Green
Set-Location "$root\backend"
pip install -r requirements.txt --quiet

# --- Start server ---
Write-Host "[INFO] Starting server at http://localhost:8000" -ForegroundColor Green
Write-Host "[INFO] Open http://localhost:8000 in your browser" -ForegroundColor Cyan
python main.py
