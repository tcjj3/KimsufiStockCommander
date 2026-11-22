$ErrorActionPreference = "Stop"
$Startup = [Environment]::GetFolderPath('Startup')
$Cmd = Join-Path $Startup 'Kimsufi Stock Commander.cmd'
if (Test-Path -LiteralPath $Cmd) { Remove-Item -LiteralPath $Cmd -Force }
Write-Host "Autostart removed:" -ForegroundColor Green
Write-Host $Cmd