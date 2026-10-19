# Run AI Work Factory automatically on Windows with Task Scheduler.
#
#   scripts\install-task.ps1               register, start at every logon of this user, start now (no admin)
#   scripts\install-task.ps1 -AtStartup    start when Windows boots, nobody needs to log on (elevated PowerShell;
#                                          Windows asks for this account's password once and stores it itself)
#   scripts\install-task.ps1 -AtStartup -S4U   same, without storing a password (git over SSH keys only)
#   scripts\install-task.ps1 -NoStart      register only
#   scripts\install-task.ps1 -Status       show task state and the last log lines
#   scripts\install-task.ps1 -Restart      after editing .env or after git pull + bootstrap
#   scripts\install-task.ps1 -Stop         stop and keep it off (until -Start)
#   scripts\install-task.ps1 -Start        turn it back on
#   scripts\install-task.ps1 -Uninstall    stop and remove the task
#
# Runs `awf start --supervise` hidden (pythonw, no console window) in your own logon session, so it
# uses your git credentials and this checkout's .env (AWF_ROLE decides what it does; normally
# "worker" on this PC). Output goes to var\logs\awf.log (rotated at 10 MB).
# Task Scheduler does not restart a task whose process dies, so a second trigger re-launches it every
# 5 minutes; while it is running those launches are ignored (MultipleInstances IgnoreNew).
# -AtStartup runs the same thing as this account "whether the user is logged on or not": after a power cut
# the PC works again as soon as Windows is up. The password goes from the Windows credential prompt straight
# to Task Scheduler (which keeps it encrypted); this script never writes or prints it. With -S4U nothing is
# stored, but Windows then gives the task no access to saved credentials (Git Credential Manager, network
# shares): use it only when every repository is cloned over SSH.
# Run scripts\bootstrap.ps1 first.
param(
    [switch]$Uninstall,
    [switch]$NoStart,
    [switch]$Restart,
    [switch]$Status,
    [switch]$Stop,
    [switch]$Start,
    [switch]$AtStartup,
    [switch]$S4U,
    [string]$TaskName = "AI Work Factory",
    [ValidateRange(1, 60)][int]$WatchdogMinutes = 5
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$log = Join-Path $root "var\logs\awf.log"
$user = "$env:USERDOMAIN\$env:USERNAME"

function Get-AwfTask { Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }

function Get-AwfProcesses {
    # The venv's pythonw.exe is a launcher that starts the base interpreter as a child process; match
    # both by command line (it contains this checkout's log path).
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains("awf.cli start") -and $_.CommandLine.Contains($root) }
}

function Stop-AwfTask {
    $task = Get-AwfTask
    if ($task -and $task.State -eq "Running") { Stop-ScheduledTask -TaskName $TaskName }
    Get-AwfProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Show-AwfStatus {
    $task = Get-AwfTask
    if (-not $task) { Write-Host "Task '$TaskName' is not installed."; return }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    $procs = @(Get-AwfProcesses)
    $mode = if ($task.Principal.LogonType -eq "Interactive") { "at logon of $($task.Principal.UserId)" } `
        else { "at Windows startup as $($task.Principal.UserId) ($($task.Principal.LogonType))" }
    Write-Host "Task      : $TaskName ($($task.State)), starts $mode"
    Write-Host ("Last run  : {0}  result 0x{1:X}" -f $info.LastRunTime, $info.LastTaskResult)
    Write-Host "Processes : $($procs.Count) (pid $($procs.ProcessId -join ', '))"
    Write-Host "Log       : $log"
    if (Test-Path $log) { Get-Content $log -Tail 8 | ForEach-Object { Write-Host "  $_" } }
}

function Assert-Installed {
    if (-not (Get-AwfTask)) { throw "Task '$TaskName' is not installed; run scripts\install-task.ps1 first." }
}

if ($Status) { Show-AwfStatus; return }

if ($Uninstall) {
    Stop-AwfTask
    if (Get-AwfTask) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
    Write-Host "Task '$TaskName' removed (data in $root\var is untouched)."
    return
}

if ($Stop) {
    Assert-Installed
    Disable-ScheduledTask -TaskName $TaskName | Out-Null  # otherwise the watchdog restarts it
    Stop-AwfTask
    Write-Host "Task '$TaskName' stopped and disabled (turn it back on with -Start)."
    return
}

if ($Start -or $Restart) {
    Assert-Installed
    Enable-ScheduledTask -TaskName $TaskName | Out-Null
    Stop-AwfTask
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 5
    Show-AwfStatus
    return
}

if (-not (Test-Path $pythonw)) { throw "Missing $pythonw - run scripts\bootstrap.ps1 first." }
if (-not (Test-Path (Join-Path $root ".env"))) { throw "Missing $root\.env - run scripts\bootstrap.ps1 first." }
$role = (Select-String -Path (Join-Path $root ".env") -Pattern "^AWF_ROLE=(.*)$" | Select-Object -Last 1).Matches.Groups[1].Value
if (-not $role) { $role = "all" }

if ($S4U -and -not $AtStartup) { throw "-S4U only applies together with -AtStartup." }
if ($AtStartup) {
    $elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $elevated) {
        throw "-AtStartup needs an elevated PowerShell (right-click > Run as administrator), opened by the account " +
              "that should run the worker ($user)."
    }
}

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "-m awf.cli start --supervise --log-file `"$log`"" -WorkingDirectory $root
if ($AtStartup) {
    $first = New-ScheduledTaskTrigger -AtStartup
    $first.Delay = "PT1M"  # give the network and Tailscale a minute after boot
} else {
    $first = New-ScheduledTaskTrigger -AtLogOn -User $user
}
# Watchdog: relaunch within $WatchdogMinutes minutes if the process died (ignored while it is running).
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes($WatchdogMinutes) `
    -RepetitionInterval (New-TimeSpan -Minutes $WatchdogMinutes)
# No 72-hour limit, keep going on battery and when idle, retry if the launch itself fails.
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd
$description = "AI Work Factory (awf start, role=$role) from $root. Log: $log"

Stop-AwfTask
if ($AtStartup -and -not $S4U) {
    # "Run whether user is logged on or not": Task Scheduler needs the account password to log on by itself.
    $cred = Get-Credential -UserName $user -Message ("Password of $user (the Windows password, not the PIN). " +
        "Task Scheduler stores it encrypted to start the worker when Windows boots.")
    if (-not $cred) { throw "No password entered; nothing was changed." }
    $settings.RunOnlyIfNetworkAvailable = $true
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($first, $watchdog) -Settings $settings `
        -User $user -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force `
        -Description $description | Out-Null
    $cred = $null
    $when = "when Windows starts (no logon needed)"
} else {
    $logonType = if ($S4U) { "S4U" } else { "Interactive" }
    if ($S4U) { $settings.RunOnlyIfNetworkAvailable = $true }
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType $logonType -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($first, $watchdog) -Settings $settings `
        -Principal $principal -Force -Description $description | Out-Null
    $when = if ($S4U) { "when Windows starts (no logon needed, no stored password)" } else { "at logon" }
}
Write-Host "Registered task '$TaskName' (role=$role, user=$user): starts $when, hidden, relaunched within $WatchdogMinutes min if it dies."

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 5
    Show-AwfStatus
}
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  .\scripts\install-task.ps1 -Status      # state + last log lines"
Write-Host "  .\scripts\install-task.ps1 -Restart     # after editing .env or git pull + bootstrap"
Write-Host "  Get-Content var\logs\awf.log -Wait      # live log"
Write-Host "  .\scripts\install-task.ps1 -Stop        # stop and keep it off (-Start to resume)"
Write-Host "  .\scripts\install-task.ps1 -Uninstall   # remove"
Write-Host ""
if ($AtStartup) {
    Write-Host "Note: the worker now runs from boot, logged on or not. Windows still sleeps per its power plan: set"
    Write-Host "sleep to 'Never' (Settings > System > Power) and, in the BIOS, 'Restore on AC power loss' = On so the"
    Write-Host "PC comes back by itself after a power cut. Changed your Windows password? Run -AtStartup again."
} else {
    Write-Host "Note: the task runs while you are logged on. Set Windows sleep to 'Never' (Settings > System >"
    Write-Host "Power) if this PC should keep working while you are away; locking the screen is fine."
    Write-Host "To run from boot without anyone logging on: .\scripts\install-task.ps1 -AtStartup (elevated)."
}
