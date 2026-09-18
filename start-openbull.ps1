# =====================================================================
# start-openbull.ps1  --  one-shot local launcher (Windows + Docker Desktop)
#
# Prereqs already done by setup: uv, Python 3.12, backend deps (uv sync),
# frontend deps (npm install), and .env with generated secrets.
#
# This script:
#   1. starts Postgres + Redis as Docker containers (creates them first run),
#   2. waits for Postgres, runs DB migrations,
#   3. launches the backend (uvicorn :8000) and frontend (vite :5173)
#      in two new PowerShell windows.
#
# Run it from this folder AFTER Docker Desktop is installed and running:
#   powershell -File .\start-openbull.ps1
# =====================================================================

$root = $PSScriptRoot
$uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
Set-Location $root

# 0. sanity: docker reachable?
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is not running. Start Docker Desktop and try again." -ForegroundColor Red
    exit 1
}

# 1. Postgres container (password + db match DATABASE_URL in .env)
docker start ob-postgres *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating ob-postgres container..." -ForegroundColor Cyan
    docker run -d --name ob-postgres `
        -e POSTGRES_PASSWORD=123456 -e POSTGRES_DB=openbull `
        -p 5432:5432 postgres:15
}

# 2. Redis container
docker start ob-redis *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating ob-redis container..." -ForegroundColor Cyan
    docker run -d --name ob-redis -p 6379:6379 redis:7
}

# 3. wait for Postgres to accept connections
Write-Host "Waiting for Postgres..." -ForegroundColor Cyan
for ($i = 0; $i -lt 40; $i++) {
    docker exec ob-postgres pg_isready -U postgres *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 1
}

# 4. migrations (idempotent)
Write-Host "Running DB migrations..." -ForegroundColor Cyan
& $uv run python migrate_all.py
if ($LASTEXITCODE -ne 0) { Write-Host "Migration failed - check output above." -ForegroundColor Red; exit 1 }

# 5. backend + frontend in their own windows
Write-Host "Launching backend (:8000) and frontend (:5173)..." -ForegroundColor Green
Start-Process powershell -ArgumentList '-NoExit', '-Command',
    "Set-Location '$root'; & '$uv' run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload"
Start-Process powershell -ArgumentList '-NoExit', '-Command',
    "Set-Location '$root\frontend'; npm run dev"

Write-Host ""
Write-Host "OpenBull is starting:" -ForegroundColor Green
Write-Host "   Frontend : http://localhost:5173"
Write-Host "   Backend  : http://127.0.0.1:8000"
Write-Host "Open the frontend URL in your browser. First run: create a user account in the app."
