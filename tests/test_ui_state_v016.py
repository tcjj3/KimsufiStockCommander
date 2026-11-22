from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "ui" / "app.js").read_text(encoding="utf-8")
HTML = (Path(__file__).resolve().parents[1] / "ui" / "index.html").read_text(encoding="utf-8")

assert "dirtySections:new Set()" in JS
assert "state.ui.settingsInitialized=true;" in JS
assert "renderSettings(forceSettings);" in JS
assert "markSectionDirty('budget')" in JS
assert "markSectionDirty('notifications')" in JS
assert "markSectionDirty('strategy')" in JS
assert "applySavedSettings(result,'notifications')" in JS
assert "applySavedSettings(result,'budget')" in JS
assert "applySavedSettings(result,'strategy')" in JS
assert "applySavedSettings(result,'runtime')" in JS

assert 'id="budgetSettingsPanel"' in HTML
assert 'id="notificationSettingsPanel"' in HTML
assert 'id="strategySettingsPanel"' in HTML

# The v0.15 bug condition must be gone.
assert "!state.ui.settingsInitialized||!state.ui.settingsDirty" not in JS

print("v0.16 UI state regression: OK")