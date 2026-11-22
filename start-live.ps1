$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $Root "KimsufiServer.json"
$cfg = @{ bindHost="127.0.0.1"; port=8765; openBrowser=$true }
if (Test-Path -LiteralPath $ConfigPath) {
  $raw = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
  foreach($p in $raw.PSObject.Properties){ if($cfg.ContainsKey($p.Name)){ $cfg[$p.Name]=$p.Value } }
}
$launchArgs = @('-3',(Join-Path $Root 'KimsufiStockCommander.py'),'--host',[string]$cfg.bindHost,'--port',[string]([int]$cfg.port))
if([bool]$cfg.openBrowser){$launchArgs += '--open-browser'}
& py.exe @launchArgs
exit $LASTEXITCODE