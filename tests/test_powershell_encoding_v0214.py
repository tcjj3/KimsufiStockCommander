from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ps1 = sorted(ROOT.rglob("*.ps1"))
assert ps1, "no PowerShell scripts found"
for path in ps1:
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), f"{path.relative_to(ROOT)} must be UTF-8 with BOM for Windows PowerShell 5.1"
    text = raw.decode("utf-8-sig")
    for ch in ("“", "”", "‘", "’"):
        assert ch not in text, f"smart quote {ch!r} remains in {path.relative_to(ROOT)}"
print(f"v0.21.4 PowerShell encoding regression: OK ({len(ps1)} scripts)")