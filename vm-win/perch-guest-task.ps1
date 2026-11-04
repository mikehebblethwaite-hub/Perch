# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# Starts Perch's Windows helper (C:\perch\perch-guest.ps1) at every sign-in, in the signed-in user's desktop session, hidden.
# Elevated (RunLevel Highest): Windows lets only administrators open the virtio-serial port. Run by perch-winvm guest.
$ErrorActionPreference = 'Stop'
$user = $env:USERNAME
$cmd = '& C:\perch\perch-guest.ps1 *> C:\perch\guest.log'
$act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$cmd`""
$trg = New-ScheduledTaskTrigger -AtLogOn -User $user
$pri = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Stop-ScheduledTask -TaskName PerchGuest -ErrorAction SilentlyContinue   # else a new start is ignored while the old one is 'running'
Register-ScheduledTask -TaskName PerchGuest -Action $act -Trigger $trg -Principal $pri -Settings $set -Force | Out-Null
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match 'perch-guest\.ps1' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Wait-Process -Id $_.ProcessId -Timeout 10 -ErrorAction SilentlyContinue }
Start-Sleep 2   # the port is let go a moment after the old helper ends
function Running { [bool](Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'perch-guest\.ps1' }) }
for ($i = 0; $i -lt 5 -and -not (Running); $i++) { Start-ScheduledTask -TaskName PerchGuest; Start-Sleep 4 }
if (-not (Running)) { throw "the helper did not start: see C:\perch\guest.log" }
"helper in: C:\perch\perch-guest.ps1, task PerchGuest (at sign-in, hidden)"
