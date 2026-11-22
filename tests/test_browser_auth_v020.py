from pathlib import Path
root=Path(__file__).resolve().parents[1];js=(root/'ui'/'app.js').read_text(encoding='utf-8');html=(root/'ui'/'index.html').read_text(encoding='utf-8')
assert "Authorization':`Bearer ${token}`" in js
assert "sessionStorage.getItem('kscApiToken')" in js and "localStorage.getItem('kscApiToken')" in js
assert "apiBlob(`/api/analytics/export" in js and "window.location.href=`/api/analytics/export" not in js
assert 'id="authDialog"' in html and 'id="apiTokenInput"' in html and 'id="rememberApiToken"' in html
assert 'await initAuth()' in js
print('v0.20 browser auth tests: OK')