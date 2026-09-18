# Synapse one-click demo starter.
# Starts the stack, waits for it to be ready, seeds demo data, submits the
# GO and NO_GO demo claims, and opens all three browser tabs. Safe to run
# from a cold stopped state or with everything already up and seeded.

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

function Fail($message) {
    Write-Host ""
    Write-Host "PROBLEM: $message" -ForegroundColor Red
    Write-Host "Press Enter to close this window."
    Read-Host | Out-Null
    exit 1
}

Write-Host "Starting system..."

# --- Docker Desktop check -------------------------------------------------
$null = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "Docker Desktop doesn't seem to be running. Open Docker Desktop from the Start menu, wait about a minute for it to finish starting, then double-click this launcher again."
}

# --- Start the stack (idempotent: docker compose no-ops on already-up containers) ---
$null = docker compose up --build -d 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "Docker couldn't start the system. Try closing and reopening Docker Desktop, then run this again. If it keeps failing, check that nothing else is using port 8000, 5432, or 6379."
}

# --- Wait for the app to actually respond on port 8000 --------------------
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        # Not ready yet - keep polling.
    }
    Start-Sleep -Seconds 2
}

if (-not $ready) {
    Fail "The system started but never responded on port 8000 after two minutes. Check Docker Desktop's dashboard for a crashed container, or try running this again."
}

Write-Host "System ready."

# --- Seed demo data (idempotent: script skips rows that already exist) ----
$env:DATABASE_URL = "postgresql+asyncpg://synapse:synapse@localhost:5432/synapse"
$env:REDIS_URL = "redis://localhost:6379/0"

$null = & ".venv\Scripts\python.exe" "scripts\seed_dev_data.py" 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "Seeding the demo data failed. Try running this again - if it keeps failing, the system may still be finishing startup."
}

# --- Submit the two demo claims (idempotent: same claim_id just re-adjudicates) ---
$goClaim = @{
    claim_id        = "CLM-DEMO-GO"
    timestamp       = "2026-09-30T10:00:00Z"
    project_id      = "PROJ-DEMO-01"
    issuer_id       = "USR-SUP-01"
    authority_level = 3
    zone_id         = "ZONE-01"
    action_type     = "MATERIAL_ENTRY"
    payload_data    = @{}
    work_type       = "NOMINAL_CIVIL"
} | ConvertTo-Json

try {
    Invoke-RestMethod -Uri "http://localhost:8000/airlock/claims" -Method Post -Body $goClaim -ContentType "application/json" | Out-Null
} catch {
    Fail "Submitting the GO demo claim failed. Try running this again."
}

$noGoClaim = @{
    claim_id        = "CLM-DEMO-NOGO"
    timestamp       = "2026-09-30T10:05:00Z"
    project_id      = "PROJ-DEMO-01"
    issuer_id       = "USR-SUP-01"
    authority_level = 3
    zone_id         = "ZONE-01"
    action_type     = "EXCAVATION_WORK"
    payload_data    = @{}
    work_type       = "EXCAVATION"
} | ConvertTo-Json

try {
    Invoke-RestMethod -Uri "http://localhost:8000/airlock/claims" -Method Post -Body $noGoClaim -ContentType "application/json" | Out-Null
} catch {
    Fail "Submitting the NO_GO demo claim failed. Try running this again."
}

Write-Host "Demo data loaded."

# --- Open the three browser tabs -------------------------------------------
Write-Host "Opening screens..."
Start-Process "http://localhost:8000/frontline/blocked/CLM-DEMO-GO"
Start-Process "http://localhost:8000/frontline/blocked/CLM-DEMO-NOGO"
Start-Process "http://localhost:8000/supervisor/blocked/CLM-DEMO-NOGO"

Write-Host ""
Write-Host "Ready. Everything succeeded - safe to start talking." -ForegroundColor Green
Write-Host "Press Enter to close this window."
Read-Host | Out-Null
