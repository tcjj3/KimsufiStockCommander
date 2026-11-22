# Kimsufi Stock Commander token helper
# Compatible with Windows PowerShell 5.1 and PowerShell 7.
#
# Default behavior:
#   generate Read + Admin tokens and write them to the current user's
#   environment variables.
#
# Examples:
#   powershell -File .\generate-api-token.ps1
#   powershell -File .\generate-api-token.ps1 -Role Read
#   powershell -File .\generate-api-token.ps1 -Role Admin -NoSetUserEnvironment
#
# We intentionally parse $args manually instead of using a parameter block.
# This avoids a Windows PowerShell 5.1 parameter-binding edge case where
# a role parameter next to a switch can be coerced into a value such as
# "Both False" before validation.

$ErrorActionPreference = "Stop"

$Role = "Both"
$SetUserEnvironment = $true

function Convert-ToBoolean([string]$Value) {
  if($null -eq $Value) { return $true }
  switch($Value.Trim().ToLowerInvariant()) {
    '1'     { return $true }
    'true'  { return $true }
    'yes'   { return $true }
    'on'    { return $true }
    '0'     { return $false }
    'false' { return $false }
    'no'    { return $false }
    'off'   { return $false }
    default { throw "无效布尔值 '$Value'。可用：true/false, yes/no, 1/0, on/off。" }
  }
}

for($i = 0; $i -lt $args.Count; $i++) {
  $arg = [string]$args[$i]
  switch -Regex ($arg) {
    '^-Role$' {
      if($i + 1 -ge $args.Count) { throw "-Role 后需要 Read、Admin、Both 或 Legacy。" }
      $i++
      $Role = [string]$args[$i]
      continue
    }
    '^-SetUserEnvironment$' {
      if($i + 1 -lt $args.Count -and -not ([string]$args[$i + 1]).StartsWith('-')) {
        $i++
        $SetUserEnvironment = Convert-ToBoolean ([string]$args[$i])
      } else {
        $SetUserEnvironment = $true
      }
      continue
    }
    '^-NoSetUserEnvironment$' {
      $SetUserEnvironment = $false
      continue
    }
    '^-Help$|^--help$|^-h$|^/\?$' {
      Write-Host "Kimsufi Stock Commander Token Generator" -ForegroundColor Cyan
      Write-Host ""
      Write-Host "默认：生成 Read + Admin Token，并写入当前用户环境变量。"
      Write-Host ""
      Write-Host "用法："
      Write-Host "  powershell -File .\generate-api-token.ps1"
      Write-Host "  powershell -File .\generate-api-token.ps1 -Role Read"
      Write-Host "  powershell -File .\generate-api-token.ps1 -Role Admin"
      Write-Host "  powershell -File .\generate-api-token.ps1 -Role Both"
      Write-Host "  powershell -File .\generate-api-token.ps1 -Role Legacy"
      Write-Host "  powershell -File .\generate-api-token.ps1 -NoSetUserEnvironment"
      Write-Host ""
      Write-Host "兼容：-SetUserEnvironment、-SetUserEnvironment Yes/No。"
      exit 0
    }
    default {
      throw "未知参数 '$arg'。使用 -Help 查看用法。"
    }
  }
}

$validRoles = @('Read', 'Admin', 'Both', 'Legacy')
$canonicalRole = $null
foreach($candidate in $validRoles) {
  if($candidate.Equals($Role, [System.StringComparison]::OrdinalIgnoreCase)) {
    $canonicalRole = $candidate
    break
  }
}
if($null -eq $canonicalRole) {
  throw "无效 Role '$Role'。可用：Read、Admin、Both、Legacy。"
}
$Role = $canonicalRole

function New-KscToken {
  $bytes = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
}

function Emit-Token([string]$Name, [string]$Label) {
  $token = New-KscToken
  if($SetUserEnvironment) {
    [Environment]::SetEnvironmentVariable($Name, $token, "User")
    Write-Host "已写入当前用户环境变量 $Name。请重启 Commander/Tray 后生效。" -ForegroundColor Green
  }
  Write-Host ""
  Write-Host "$Label ($Name)：" -ForegroundColor Cyan
  Write-Host $token -ForegroundColor Yellow
}

if($Role -eq 'Read' -or $Role -eq 'Both') {
  Emit-Token 'KSC_READ_TOKEN' 'Read Token'
}
if($Role -eq 'Admin' -or $Role -eq 'Both') {
  Emit-Token 'KSC_ADMIN_TOKEN' 'Admin Token'
}
if($Role -eq 'Legacy') {
  Emit-Token 'KSC_API_TOKEN' 'Legacy single Admin Token'
}

Write-Host ""
if($SetUserEnvironment) {
  Write-Host "环境变量已写入当前用户。现有 Commander/Tray 进程不会自动看到新值，请完全重启它们。" -ForegroundColor Green
} else {
  Write-Host "本次仅生成 Token，没有修改用户环境变量。" -ForegroundColor DarkYellow
}
Write-Host "Read Token 可长期用于受信任客户端；Admin Token 建议只做临时提权，不写入 JSON。" -ForegroundColor DarkYellow
