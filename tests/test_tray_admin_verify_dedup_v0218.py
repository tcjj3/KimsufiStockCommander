from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tray = (ROOT / "KimsufiTray.ps1").read_text(encoding="utf-8-sig")
py = (ROOT / "KimsufiStockCommander.py").read_text(encoding="utf-8")

assert 'APP_VERSION = "0.21.9"' in py

# One Tray per backend endpoint: prevents old/new hidden tray instances from
# producing independent dialogs for the same server.
assert 'KimsufiStockCommanderTray_' in tray
assert 'WaitOne(0, $false)' in tray
assert 'Release-TrayInstanceMutex' in tray

# Admin verification uses a generation id. A stale/duplicate completion is
# discarded before it can display any result.
assert '$script:AdminVerifyGeneration = 0' in tray
assert 'VerifyId = $verifyId' in tray
assert 'if ($verifyId -ne [int]$script:AdminVerifyGeneration) { return }' in tray
assert '$script:AdminVerifyGeneration++' in tray

# Only explicit 401 is allowed to claim an invalid Admin token.
needle = 'if ($status -eq 401) {'
assert needle in tray
assert 'Admin Token 无效或已变更。' in tray
assert 'Token 尚未验证' in tray
assert '这不代表 Token 错误' in tray

# Old generic semantics must not return.
assert 'Admin Token 验证失败。' not in tray

print('v0.21.8 Tray Admin verify de-dup/single-instance regression: OK')