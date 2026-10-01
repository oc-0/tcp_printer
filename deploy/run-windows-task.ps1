param(
    [int]$HealthIntervalSeconds = 15,
    [int]$HealthTimeoutSeconds = 5,
    [int]$MaxHealthFailures = 3
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$dataDir = Join-Path $projectRoot "data"
$stdoutLog = Join-Path $dataDir "windows-task.stdout.log"
$stderrLog = Join-Path $dataDir "windows-task.stderr.log"
$port = 8080

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Virtual environment Python was not found: $pythonPath"
}

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$envFile = Join-Path $projectRoot ".env"
if (Test-Path -LiteralPath $envFile) {
    $portLine = Select-String -LiteralPath $envFile -Pattern '^\s*TCP_PRINTER_PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($portLine -and $portLine.Matches.Count -gt 0) {
        $port = [int]$portLine.Matches[0].Groups[1].Value
    }
}

$child = $null

function Stop-Child {
    if ($script:child -and -not $script:child.HasExited) {
        Stop-Process -Id $script:child.Id -Force -ErrorAction SilentlyContinue
        try { $script:child.WaitForExit(5000) } catch { }
    }
    $script:child = $null
}

function Start-Child {
    Stop-Child
    $script:child = Start-Process -FilePath $pythonPath -ArgumentList @("run.py") -WorkingDirectory $projectRoot -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
}

try {
    Start-Child
    $failures = 0
    while ($true) {
        Start-Sleep -Seconds $HealthIntervalSeconds
        if (-not $child -or $child.HasExited) {
            Start-Child
            $failures = 0
            continue
        }
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/health" -TimeoutSec $HealthTimeoutSeconds | Out-Null
            $failures = 0
        } catch {
            $failures += 1
            if ($failures -ge $MaxHealthFailures) {
                Add-Content -LiteralPath $stderrLog -Value "$(Get-Date -Format o) health check failed $failures times; restarting server."
                Start-Child
                $failures = 0
            }
        }
    }
} finally {
    Stop-Child
}
