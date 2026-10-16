param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$taskName = "TCP Printer"
$projectRoot = Split-Path -Parent $PSScriptRoot
$monitorPath = Join-Path $PSScriptRoot "run-windows-task.ps1"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$firewallRulePrefix = "TCP Printer HTTP"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ConfiguredPort {
    $envFile = Join-Path $projectRoot ".env"
    if (Test-Path -LiteralPath $envFile) {
        $portLine = Select-String -LiteralPath $envFile -Pattern '^\s*TCP_PRINTER_PORT\s*=\s*(\d+)' | Select-Object -First 1
        if ($portLine -and $portLine.Matches.Count -gt 0) {
            return [int]$portLine.Matches[0].Groups[1].Value
        }
    }
    return 8080
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Virtual environment Python was not found: $pythonPath"
}
if (-not (Test-Path -LiteralPath $monitorPath)) {
    throw "Windows task monitor was not found: $monitorPath"
}

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task: $taskName"
    exit 0
}

if (-not (Test-Administrator)) {
    $powershellPath = Join-Path $PSHOME "powershell.exe"
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $elevated = Start-Process -FilePath $powershellPath -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    if ($elevated.ExitCode -ne 0) {
        throw "Elevated deployment script failed with exit code $($elevated.ExitCode)."
    }
    exit 0
}

$port = Get-ConfiguredPort
$firewallRuleName = "$firewallRulePrefix $port"
if (-not (Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $firewallRuleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile Any -RemoteAddress LocalSubnet | Out-Null
    Write-Output "Added persistent LAN firewall rule for TCP port $port."
} else {
    Write-Output "Persistent LAN firewall rule already exists for TCP port $port."
}

$userId = "$env:USERDOMAIN\$env:USERNAME"
$powershellPath = Join-Path $PSHOME "powershell.exe"
$action = New-ScheduledTaskAction -Execute $powershellPath -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$monitorPath`"" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
# New-ScheduledTaskPrincipal uses the PowerShell enum name "Interactive".
# "InteractiveToken" is accepted by schtasks.exe but not by this cmdlet.
# Word COM and the local printer do not require elevation; Limited keeps the
# task usable for a normal desktop user and avoids unnecessary UAC prompts.
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "TCP Printer Windows self-service print" -Force | Out-Null
Write-Output "Registered scheduled task: $taskName"
Write-Output "It starts when $userId logs on. Run Start-ScheduledTask -TaskName '$taskName' to start it now."
