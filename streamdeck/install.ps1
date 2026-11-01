# Installs the ClickForge plugin into Stream Deck and restarts the Stream Deck app.
$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "com.a2m33.clickforge.sdPlugin"
$dst = Join-Path $env:APPDATA "Elgato\StreamDeck\Plugins\com.a2m33.clickforge.sdPlugin"

Push-Location $src
npm install --omit=dev --no-audit --no-fund | Out-Null
Pop-Location

Get-Process StreamDeck -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
Copy-Item $src $dst -Recurse

$exe = Join-Path $env:ProgramFiles "Elgato\StreamDeck\StreamDeck.exe"
if (Test-Path $exe) { Start-Process $exe }
Write-Host "Installed to $dst"
