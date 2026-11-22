from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tray = (ROOT / "KimsufiTray.ps1").read_text(encoding="utf-8-sig")
py = (ROOT / "KimsufiStockCommander.py").read_text(encoding="utf-8")

assert 'APP_VERSION = "0.21.9"' in py
assert 'function New-HttpFailureResult' in tray
assert 'FailureKind = $kind' in tray
assert 'TaskCanceledException' in tray
assert 'function Get-HttpFailureText' in tray

# Admin verification must distinguish actual auth rejection from transport failure.
admin = tray[tray.index("'admin-verify' {"):tray.index("'admin-post' {")]
assert '$status -eq 401' in admin
assert 'Admin Token 无效或已变更。' in admin
assert '$kind -eq "timeout"' in admin
assert 'Admin Token 尚未验证' in admin
assert '$kind -eq "network"' in admin
assert '无法连接 Commander；Admin Token 尚未验证。' in admin
assert '$status -ge 500' in admin
assert 'Admin Token 尚未验证' in admin

# Probe auth may call a token invalid only for an actual HTTP auth response.
probe = tray[tray.index("'probe-auth' {"):tray.index("'state' {")]
auth_gate = probe.index('$status -eq 401 -or $status -eq 403')
invalid = probe.index('未认证：Token 无效或已变更')
assert invalid > auth_gate
assert '请求超时（自动重试）' in probe
assert '无法连接（自动重试）' in probe

# Async completion must attach a transport failure kind instead of flattening all errors.
assert '$result = New-HttpFailureResult $_.Exception' in tray
assert "FailureKind = $(if ($ok) { '' } else { 'http' })" in tray

print('v0.21.7 Tray network error semantics regression: OK')