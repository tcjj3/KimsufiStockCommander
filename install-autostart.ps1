$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Startup = [Environment]::GetFolderPath('Startup')
$Cmd = Join-Path $Startup 'Kimsufi Stock Commander.cmd'
$Tray = Join-Path $Root 'KimsufiTray.ps1'
$line = '@echo off' + "`r`n" + 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Tray + '"' + "`r`n"
Set-Content -LiteralPath $Cmd -Value $line -Encoding ASCII
Write-Host "Installed current-user autostart:" -ForegroundColor Green
Write-Host $Cmd
Write-Host "It starts the tray after Windows logon. No administrator rights are required."