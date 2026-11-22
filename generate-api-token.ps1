param(
  [string]$EnvName = "KSC_API_TOKEN",
  [switch]$SetUserEnvironment
)
$ErrorActionPreference = "Stop"
$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
if($SetUserEnvironment){
  [Environment]::SetEnvironmentVariable($EnvName,$token,"User")
  Write-Host "已写入当前用户环境变量 $EnvName。请重启 Commander/Tray 后生效。" -ForegroundColor Green
}
Write-Host ""
Write-Host "API Token（请复制到需要访问 Commander 的浏览器/Tray 所在机器）：" -ForegroundColor Cyan
Write-Host $token -ForegroundColor Yellow
Write-Host ""
Write-Host "服务器启用后，不要把这个 Token 写入 KimsufiServer.json / KimsufiTray.json。" -ForegroundColor DarkYellow