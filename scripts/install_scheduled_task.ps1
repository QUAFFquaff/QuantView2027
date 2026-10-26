param(
    [string]$TaskName = "QuantView2027-Daily",
    [string]$RunAt = "18:10"
)

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if ([TimeZoneInfo]::Local.Id -ne "China Standard Time") {
    throw "This installer requires the Windows China Standard Time zone (Asia/Shanghai). No task was installed."
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Project Python environment not found: $PythonPath"
}

if ($RunAt -notmatch '^([01][0-9]|2[0-3]):[0-5][0-9]$') {
    throw "RunAt must use HH:mm format"
}
$TimeParts = $RunAt.Split(":")

$StartTime = (Get-Date).Date.AddHours([int]$TimeParts[0]).AddMinutes([int]$TimeParts[1])
$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "-m quantview run-daily --trigger scheduled" `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $StartTime
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Generate the QuantView2027 LH/JD/C daily research report" `
    -Force

Write-Host "Installed scheduled task '$TaskName' at $RunAt on weekdays."

