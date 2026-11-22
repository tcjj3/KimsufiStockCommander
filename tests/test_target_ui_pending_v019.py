from pathlib import Path
JS = (Path(__file__).resolve().parents[1] / "ui" / "app.js").read_text(encoding="utf-8")
assert "pendingTargets:new Map()" in JS
assert "function setTargetPending" in JS
assert "function reconcilePendingTargets" in JS
assert "setTargetPending(id,optimistic); renderTable();" in JS
assert "reconcilePendingTargets(fresh);" in JS
assert "const targetView=mergedTargets();" in JS
print("v0.19 target pending UI regression: OK")