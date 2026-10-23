param([ValidateSet('start','stop','logs','status','update','check')][string]$Action = 'start', [string]$EnvFile = '.state/local/compose.env')
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $ProductPython = if (Test-Path .venv/Scripts/python.exe) { ".venv/Scripts/python.exe" } else { "python" }
    switch ($Action) {
        start { docker compose --env-file $EnvFile -f compose.yaml up -d --build --wait }
        stop { docker compose --env-file $EnvFile -f compose.yaml down }
        logs { docker compose --env-file $EnvFile -f compose.yaml logs --tail 100 -f }
        status { docker compose --env-file $EnvFile -f compose.yaml ps }
        update { docker compose --env-file $EnvFile -f compose.yaml up -d --build --wait }
        check { & $ProductPython scripts/check.py }
    }
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
