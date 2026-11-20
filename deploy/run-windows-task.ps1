param(
    [int]$HealthIntervalSeconds = 15,
    [int]$HealthTimeoutSeconds = 5,
    [int]$MaxHealthFailures = 3,
    [int]$LogRetentionDays = 30,
    [int]$MaxLogRuns = 50
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$dataDir = Join-Path $projectRoot "data"
$logDir = Join-Path $dataDir "windows-task"
$monitorLog = Join-Path $logDir "monitor.log"
$statePath = Join-Path $logDir "state.json"
$recoveryReportPath = Join-Path $logDir "last-recovery.json"
$port = 8080
$child = $null
$runId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmssfff"), $PID
$script:stdoutLog = $null
$script:stderrLog = $null
$script:restartCount = 0
$script:lastHealth = "not_checked"
$script:lastHealthAt = $null
$script:lastError = $null
$script:lastChildExitCode = $null
$script:monitorStatus = "starting"
$script:startedAt = (Get-Date)
$script:previousState = $null

function Write-MonitorLog {
    param(
        [ValidateSet("INFO", "WARN", "ERROR")]
        [string]$Level,
        [string]$Message
    )
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "o"), $Level, $Message
    Add-Content -LiteralPath $monitorLog -Value $line -Encoding UTF8
}

function Write-State {
    $state = [ordered]@{
        run_id = $runId
        monitor_status = $script:monitorStatus
        monitor_pid = $PID
        child_pid = if ($script:child) { $script:child.Id } else { $null }
        started_at = $script:startedAt.ToUniversalTime().ToString("o")
        last_health_at = $script:lastHealthAt
        last_health = $script:lastHealth
        last_error = $script:lastError
        last_child_exit_code = $script:lastChildExitCode
        restart_count = $script:restartCount
        stdout_log = $script:stdoutLog
        stderr_log = $script:stderrLog
        monitor_log = $monitorLog
    }
    $temporaryState = "{0}.tmp" -f $statePath
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporaryState -Encoding UTF8
    Move-Item -LiteralPath $temporaryState -Destination $statePath -Force
}

function Rotate-MonitorLog {
    $maxBytes = 5MB
    if (-not (Test-Path -LiteralPath $monitorLog)) {
        return
    }
    $item = Get-Item -LiteralPath $monitorLog -ErrorAction SilentlyContinue
    if ($item -and $item.Length -ge $maxBytes) {
        $archive = Join-Path $logDir ("monitor-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmssfff"))
        Move-Item -LiteralPath $monitorLog -Destination $archive -Force
    }
}

function Get-ComputerBootTime {
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        return ([DateTime]$os.LastBootUpTime).ToUniversalTime().ToString("o")
    } catch {
        return $null
    }
}

function Get-RecoveryEvents {
    param([datetime]$Since)
    $events = @()
    try {
        $events += Get-WinEvent -FilterHashtable @{ LogName = "System"; StartTime = $Since } -ErrorAction Stop |
            Where-Object { $_.Id -in @(41, 1074, 6005, 6006, 6008, 1001) } |
            Select-Object -First 30 TimeCreated, ProviderName, Id, LevelDisplayName, Message
    } catch {
        $events += [pscustomobject]@{ TimeCreated = Get-Date; ProviderName = "monitor"; Id = 0; LevelDisplayName = "Warning"; Message = "无法读取 System 事件日志: $($_.Exception.Message)" }
    }
    return @($events)
}

function Write-RecoveryReport {
    param([switch]$IncludeEvents)
    if (-not $script:previousState) {
        return
    }
    try {
        $previous = $script:previousState
        if (-not $previous.run_id -or $previous.run_id -eq $runId -or $previous.monitor_status -eq "stopped") {
            return
        }
        $previousStarted = (Get-Date).AddHours(-24)
        try { $previousStarted = [datetime]$previous.started_at } catch { }
        $systemEvents = if ($IncludeEvents) { Get-RecoveryEvents -Since $previousStarted } else { @() }
        $report = [ordered]@{
            detected_at = (Get-Date).ToUniversalTime().ToString("o")
            current_run_id = $runId
            current_boot_time = if ($IncludeEvents) { Get-ComputerBootTime } else { $null }
            previous_run_id = $previous.run_id
            previous_monitor_pid = $previous.monitor_pid
            previous_child_pid = $previous.child_pid
            previous_started_at = $previous.started_at
            previous_last_health = $previous.last_health
            previous_last_health_at = $previous.last_health_at
            previous_last_error = $previous.last_error
            previous_last_child_exit_code = $previous.last_child_exit_code
            previous_stdout_log = $previous.stdout_log
            previous_stderr_log = $previous.stderr_log
            system_events = $systemEvents
            assessment = "上一次监控状态未正常结束，可能是整机重启、断电、蓝屏、计划任务被终止或监控进程崩溃。请结合 system_events、stderr 日志和 Windows 可靠性监视器判断。"
        }
        $temporaryReport = "{0}.tmp" -f $recoveryReportPath
        $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporaryReport -Encoding UTF8
        Move-Item -LiteralPath $temporaryReport -Destination $recoveryReportPath -Force
        Write-MonitorLog "WARN" ("发现上一次监控未正常结束，已生成恢复报告: {0}" -f $recoveryReportPath)
        foreach ($event in $systemEvents) {
            Write-MonitorLog "WARN" ("恢复证据 System EventId={0}, Provider={1}, Time={2}, Message={3}" -f $event.Id, $event.ProviderName, $event.TimeCreated, $event.Message)
        }
    } catch {
        Write-MonitorLog "WARN" ("生成恢复报告失败: {0}" -f $_.Exception.Message)
    }
}

function Get-PortFromEnvironment {
    $envFile = Join-Path $projectRoot ".env"
    if (-not (Test-Path -LiteralPath $envFile)) {
        return 8080
    }
    $portLine = Select-String -LiteralPath $envFile -Pattern '^\s*TCP_PRINTER_PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($portLine -and $portLine.Matches.Count -gt 0) {
        return [int]$portLine.Matches[0].Groups[1].Value
    }
    return 8080
}

function Collect-RecentApplicationErrors {
    param([int]$LookbackMinutes = 10)
    try {
        $since = (Get-Date).AddMinutes(-[Math]::Max(1, $LookbackMinutes))
        $events = Get-WinEvent -FilterHashtable @{ LogName = "Application"; StartTime = $since; Id = 1000, 1001, 1002, 1003 } -MaxEvents 50 -ErrorAction Stop |
            Where-Object {
                $_.ProviderName -match "Application Error|Windows Error Reporting" -or
                $_.Message -match "python|run.py|TCP Printer"
            } |
            Select-Object -First 20 TimeCreated, ProviderName, Id, LevelDisplayName, Message
        if ($events) {
            Add-Content -LiteralPath $monitorLog -Value "--- recent Windows Application errors ---" -Encoding UTF8
            foreach ($event in $events) {
                Add-Content -LiteralPath $monitorLog -Value ($event | Format-List | Out-String).TrimEnd() -Encoding UTF8
            }
            Add-Content -LiteralPath $monitorLog -Value "--- end Windows Application errors ---" -Encoding UTF8
        }
    } catch {
        Write-MonitorLog "WARN" ("无法读取 Windows Application 事件日志: {0}" -f $_.Exception.Message)
    }
}

function Stop-Child {
    if ($script:child) {
        if (-not $script:child.HasExited) {
            Write-MonitorLog "WARN" ("停止子进程 PID={0}" -f $script:child.Id)
            Stop-Process -Id $script:child.Id -Force -ErrorAction SilentlyContinue
            try { $script:child.WaitForExit(5000) } catch { }
        }
        $script:lastChildExitCode = $script:child.ExitCode
        Write-MonitorLog "INFO" ("子进程 PID={0} 已退出，ExitCode={1}" -f $script:child.Id, $script:lastChildExitCode)
    }
    $script:child = $null
    Write-State
}

function Start-Child {
    Stop-Child
    $script:restartCount += 1
    $script:lastError = $null
    $script:lastHealth = "starting"
    $script:stdoutLog = Join-Path $logDir ("server-{0}-attempt{1}.stdout.log" -f $runId, $script:restartCount)
    $script:stderrLog = Join-Path $logDir ("server-{0}-attempt{1}.stderr.log" -f $runId, $script:restartCount)
    Write-MonitorLog "INFO" ("启动 Python 服务，第 {0} 次，日志: stdout={1}; stderr={2}" -f $script:restartCount, $script:stdoutLog, $script:stderrLog)
    $script:child = Start-Process -FilePath $pythonPath -ArgumentList @("run.py") -WorkingDirectory $projectRoot -RedirectStandardOutput $script:stdoutLog -RedirectStandardError $script:stderrLog -PassThru
    Write-MonitorLog "INFO" ("Python 服务已启动，PID={0}，命令={1} run.py，工作目录={2}" -f $script:child.Id, $pythonPath, $projectRoot)
    Write-State
}

function Remove-OldLogs {
    $cutoff = (Get-Date).AddDays(-[Math]::Max(1, $LogRetentionDays))
    Get-ChildItem -LiteralPath $logDir -Filter "server-*.log" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    $runs = @(Get-ChildItem -LiteralPath $logDir -Filter "server-*.stdout.log" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if ($runs.Count -gt [Math]::Max(1, $MaxLogRuns)) {
        $runs | Select-Object -Skip ([Math]::Max(1, $MaxLogRuns)) | ForEach-Object {
            $base = $_.FullName.Substring(0, $_.FullName.Length - ".stdout.log".Length)
            Remove-Item -LiteralPath $_.FullName, "$base.stderr.log" -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Virtual environment Python was not found: $pythonPath"
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$port = Get-PortFromEnvironment
Rotate-MonitorLog
Remove-OldLogs
$script:previousState = $null
if (Test-Path -LiteralPath $statePath) {
    try { $script:previousState = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Write-MonitorLog "WARN" ("读取上一次状态失败: {0}" -f $_.Exception.Message) }
}
Write-MonitorLog "INFO" ("监控启动，RunId={0}，主机={1}，用户={2}\{3}，PowerShell={4}，Python={5}，端口={6}" -f $runId, $env:COMPUTERNAME, $env:USERDOMAIN, $env:USERNAME, $PSVersionTable.PSVersion, $pythonPath, $port)
Write-MonitorLog "INFO" ("参数 HealthIntervalSeconds={0}, HealthTimeoutSeconds={1}, MaxHealthFailures={2}, LogRetentionDays={3}, MaxLogRuns={4}" -f $HealthIntervalSeconds, $HealthTimeoutSeconds, $MaxHealthFailures, $LogRetentionDays, $MaxLogRuns)
Write-RecoveryReport
Write-State

try {
    $script:monitorStatus = "running"
    Write-State
    Start-Child
    # 先让网页服务启动，再读取系统事件，避免 WMI/Event Log 阻塞网页启动。
    Write-RecoveryReport -IncludeEvents
    Collect-RecentApplicationErrors -LookbackMinutes 60
    $failures = 0
    while ($true) {
        Start-Sleep -Seconds $HealthIntervalSeconds
        if (-not $child -or $child.HasExited) {
            if ($child) {
                $script:lastChildExitCode = $child.ExitCode
                Write-MonitorLog "ERROR" ("检测到 Python 服务已退出，PID={0}，ExitCode={1}" -f $child.Id, $script:lastChildExitCode)
                Collect-RecentApplicationErrors
            } else {
                Write-MonitorLog "ERROR" "检测到 Python 服务进程不存在"
            }
            Start-Child
            $failures = 0
            continue
        }
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/health" -TimeoutSec $HealthTimeoutSeconds | Out-Null
            $script:lastHealth = "ok"
            $script:lastHealthAt = (Get-Date).ToUniversalTime().ToString("o")
            $script:lastError = $null
            $failures = 0
        } catch {
            $failures += 1
            $script:lastHealth = "failed"
            $script:lastHealthAt = (Get-Date).ToUniversalTime().ToString("o")
            $script:lastError = $_.Exception.Message
            Write-MonitorLog "WARN" ("健康检查失败 {0}/{1}，PID={2}，地址=http://127.0.0.1:{3}，原因={4}" -f $failures, $MaxHealthFailures, $child.Id, $port, $_.Exception.Message)
            if ($failures -ge $MaxHealthFailures) {
                Write-MonitorLog "ERROR" ("连续 {0} 次健康检查失败，准备重启 Python 服务" -f $failures)
                Collect-RecentApplicationErrors
                Start-Child
                $failures = 0
            }
        }
        Write-State
    }
} catch {
    $script:lastError = $_.Exception.ToString()
    Write-MonitorLog "ERROR" ("监控脚本异常退出: {0}" -f $script:lastError)
    Collect-RecentApplicationErrors
    Write-State
    throw
} finally {
    $script:monitorStatus = "stopping"
    Write-State
    Write-MonitorLog "WARN" "监控脚本正在退出"
    Stop-Child
    $script:monitorStatus = "stopped"
    Write-State
}
