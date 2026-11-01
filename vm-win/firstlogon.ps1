# Copyright (c) 2026 Mike J Hebblethwaite. CONFIDENTIAL. First sign-in on Perch's Windows test VM (run once by autounattend).
# Never sleeps or blanks the screen (Perch watches it), SSH for building in it (key only), then writes C:\perch-ready.txt.
$done = 'C:\perch-ready.txt'
if (Test-Path $done) { exit 0 }                      # the answer disc offers this script on D:, E: and F:; run once
Start-Transcript -Path C:\perch-firstlogon.log -Append
powercfg /change standby-timeout-ac 0; powercfg /change monitor-timeout-ac 0; powercfg /change hibernate-timeout-ac 0
Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name ScreenSaveActive -Value 0
New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' -Force | Out-Null
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' -Name NoLockScreen -Value 1 -Type DWord
# SSH (key only, from the host): the OpenSSH server is a Windows feature fetched from Windows Update
$ok = $false
for ($i = 0; $i -lt 10 -and -not $ok; $i++) {
    try { Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -ErrorAction Stop | Out-Null; $ok = $true }
    catch { Write-Output "OpenSSH try $i failed: $_"; Start-Sleep 30 }
}
if ($ok) {
    $key = '@SSHKEY@'
    Set-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value $key -Encoding ascii
    icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
    New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -PropertyType String -Force | Out-Null
    $c = 'C:\ProgramData\ssh\sshd_config'
    if (-not (Test-Path $c)) { Start-Service sshd; Stop-Service sshd }      # the first start writes the default config
    (Get-Content $c) -replace '^#?PasswordAuthentication .*', 'PasswordAuthentication no' | Set-Content $c -Encoding ascii
    Set-Service sshd -StartupType Automatic; Start-Service sshd
    if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null }
}
Set-Content -Path $done -Value ("ready " + (Get-Date -Format s) + " ssh=" + $ok)
Stop-Transcript
