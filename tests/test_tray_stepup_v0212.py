from pathlib import Path

root = Path(__file__).resolve().parents[1]
ps = (root / "KimsufiTray.ps1").read_text(encoding="utf-8-sig")
py = (root / "KimsufiStockCommander.py").read_text(encoding="utf-8")

assert 'APP_VERSION = "0.21.9"' in py
assert '$script:SessionAdminToken = ""' in ps
assert '$script:AdminAvailable = $false' in ps
assert 'function Show-AdminElevationDialog' in ps
assert 'function Invoke-AdminAction' in ps
assert 'Start-CommanderGetAsync' in ps
assert '仅执行此次' in ps
assert '提升为 Admin（本次会话）' in ps
assert '🛡 降回 Read' in ps
assert '已连接 · Admin（本次 Tray 会话）' in ps
assert 'Invoke-AdminAction "立即轮询"' in ps
assert 'Invoke-AdminAction "刷新目录"' in ps
assert '$pollItem.Enabled = $true' in ps
assert '$catalogItem.Enabled = $true' in ps
assert '$readConnected -and $script:AdminAvailable' in ps
assert '服务器未配置 Admin' in ps
assert 'Get-EffectiveApiToken' in ps
assert 'Get-ConfiguredApiToken' in ps
assert 'EnvironmentVariable' not in ps[ps.index('function Show-AdminElevationDialog'):ps.index('try { Start-CommanderBackend')]
print('v0.21.7 Tray step-up regression: OK')