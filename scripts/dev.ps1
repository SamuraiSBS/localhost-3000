param([ValidateSet('start','stop','logs','status','update','check')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    switch ($Action) {
        start { docker compose up -d --build --wait }
        stop { docker compose down }
        logs { docker compose logs --tail 100 -f }
        status { docker compose ps }
        update { docker compose up -d --build --wait }
        check { python scripts/check.py }
    }
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
