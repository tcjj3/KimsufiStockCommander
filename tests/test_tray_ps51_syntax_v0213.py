from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
S=(ROOT/"KimsufiTray.ps1").read_text(encoding="utf-8-sig")

# Windows PowerShell treats smart quotation marks as quote delimiters.
for ch in ("“","”","‘","’"):
    assert ch not in S, f"smart quote remains in KimsufiTray.ps1: {ch!r}"

assert '$message.Text = "$ActionLabel 需要 Admin 权限。`r`n输入 Admin Token 后，可仅执行此次操作，或提升当前 Tray 会话。"' in S
print("v0.21.4 Tray PS5.1 smart-quote regression: OK")