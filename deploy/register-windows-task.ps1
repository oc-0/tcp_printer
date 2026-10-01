param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$taskName = "TCP Printer"
$projectRoot = Split-Path -Parent $PSScriptRoot
$monitorPath = Join-Path $PSScriptRoot "run-windows-task.ps1"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

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
