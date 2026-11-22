from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tray = (ROOT / "KimsufiTray.ps1").read_text(encoding="utf-8-sig")
assert '[System.Drawing.SystemIcons]::Shield.ToBitmap()' in tray
assert '$title.Text = "Commander 管理员授权"' in tray
assert '🛡 Commander 管理员授权' not in tray
assert 'PictureBox' in tray
print("v0.21.5 Tray native shield regression: OK")