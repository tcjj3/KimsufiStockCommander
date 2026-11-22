from pathlib import Path
import json
root = Path(__file__).resolve().parents[1]
tray = (root / "KimsufiTray.ps1").read_text(encoding="utf-8-sig")
cfg = json.loads((root / "KimsufiTray.json").read_text(encoding="utf-8"))
remote = json.loads((root / "KimsufiTray.remote-example.json").read_text(encoding="utf-8"))
assert "$BaseUrl" in tray and "serverHost" in tray and "serverPort" in tray
assert "if (-not [bool]$cfg.startBackend) { return }" in tray
assert "if ([bool]$cfg.allowStopBackend -and [bool]$cfg.startBackend)" in tray
assert remote["startBackend"] is False and remote["allowStopBackend"] is False
assert cfg["serverPort"] == 8765
print("v0.19 tray decouple regression: OK")