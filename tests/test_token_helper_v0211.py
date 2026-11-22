from pathlib import Path

root = Path(__file__).resolve().parents[1]
ps = (root / "generate-api-token.ps1").read_text(encoding="utf-8-sig")
assert "ValidateSet" not in ps
assert "[switch]$SetUserEnvironment" not in ps
assert '$Role = "Both"' in ps
assert '$SetUserEnvironment = $true' in ps
assert "$args.Count" in ps
assert "-NoSetUserEnvironment" in ps
assert "KSC_READ_TOKEN" in ps
assert "KSC_ADMIN_TOKEN" in ps
print("v0.21.1 token helper regression: OK")