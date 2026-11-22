from pathlib import Path

root = Path(__file__).resolve().parents[1]
tray = (root / "KimsufiTray.ps1").read_text(encoding="utf-8-sig")
py = (root / "KimsufiStockCommander.py").read_text(encoding="utf-8")

assert 'APP_VERSION = "0.21.9"' in py
assert '[System.Security.Cryptography.SHA256]::Create()' in tray
assert '$mutexSeed = "{0}|{1}|{2}"' in tray
assert '.Substring(0, 32)' in tray
assert r'$mutexName = "Local\\KimsufiStockCommanderTray_$mutexKey"' not in tray
assert r'$mutexName = "Local\KimsufiStockCommanderTray_$mutexKey"' in tray
assert 'New-Object -TypeName System.Threading.Mutex -ArgumentList @($false, $mutexName)' in tray
print("v0.21.9 Tray mutex naming regression: OK")