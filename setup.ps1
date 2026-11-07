# One-time setup for Windows.   Right-click > Run with PowerShell, or:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path venv)) { python -m venv venv }
.\venv\Scripts\python -m pip install -U pip
.\venv\Scripts\pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

Write-Host "downloading the offline wake-word model (~40 MB)..."
.\venv\Scripts\python -c "from src.stt import ensure_vosk_model; print(ensure_vosk_model())"

if (-not (Get-Command mpv -ErrorAction SilentlyContinue)) {
    Write-Host "`nShe needs mpv to play her voice:  winget install mpv"
}
Write-Host "`nAll set. Put a .vrm in .\models, add your key to .env, then:  .\venv\Scripts\python main.py"
