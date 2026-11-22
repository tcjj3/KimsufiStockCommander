from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
js=(ROOT/'ui/app.js').read_text(encoding='utf-8')
html=(ROOT/'ui/index.html').read_text(encoding='utf-8')

assert "dialogMandatory:false" in js
assert "function closeAuthDialog()" in js
assert "showAuthDialog(message='',mandatory=false)" in js
assert "e.target===$('#authDialog')" in js
assert "$('#authClose').addEventListener('click'" in js
assert "$('#authCancel').addEventListener('click'" in js
assert "showAuthDialog('',!state.auth.authenticated)" in js
assert 'id="authClose"' in html
assert 'id="authCancel"' in html
print('browser auth dialog v0.20.1: OK')