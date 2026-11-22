# Installs PMic to start automatically when you log into Windows.
# Run once: right-click -> Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File "Install PMic autostart.ps1"
$ErrorActionPreference = "Stop"
$pmicDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1. shortcut in the user's Startup folder
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "PMic.lnk"
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = Join-Path $pmicDir "Start PMic (background).vbs"
$sc.WorkingDirectory = $pmicDir
$sc.Description = "PMic - phone as microphone"
$sc.Save()
Write-Host "Autostart installed: $lnk"
Write-Host "PMic will start (no console window) next time you log in."
Write-Host "To remove it later, delete that shortcut."

# 2. firewall rule (needs admin; skip silently if not elevated)
$firewallOk = $false
try {
    New-NetFirewallRule -DisplayName "PMic" -Direction Inbound -Program (Get-Command pythonw -ErrorAction SilentlyContinue).Source -Protocol TCP -LocalPort 8787 -Action Allow -ErrorAction Stop | Out-Null
    $firewallOk = $true
} catch {
    # fall back to python.exe, then to manual instructions
    try {
        New-NetFirewallRule -DisplayName "PMic" -Direction Inbound -Program (Get-Command python -ErrorAction SilentlyContinue).Source -Protocol TCP -LocalPort 8787 -Action Allow -ErrorAction Stop | Out-Null
        $firewallOk = $true
    } catch {}
}
if ($firewallOk) {
    Write-Host "Firewall rule added for port 8787."
} else {
    Write-Warning "Could not add the firewall rule (needs Administrator)."
    Write-Host "Run once as Administrator:"
    Write-Host '  netsh advfirewall firewall add rule name="PMic" dir=in action=allow protocol=TCP localport=8787'
}
