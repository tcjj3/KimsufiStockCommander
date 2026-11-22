from pathlib import Path
root=Path(__file__).resolve().parents[1];js=(root/'ui'/'app.js').read_text(encoding='utf-8');html=(root/'ui'/'index.html').read_text(encoding='utf-8');css=(root/'ui'/'styles.css').read_text(encoding='utf-8')
assert "sessionStorage.setItem('kscAdminToken'" in js
assert "localStorage.setItem('kscAdminToken'" not in js
assert "data.error==='admin_required'" in js and 'requestAdminAuthorization' in js
assert "仅执行此次操作" in html and "提升为 Admin（本次会话）" in html
assert 'id="adminAuthDialog"' in html and 'id="adminTokenInput"' in html
assert 'id="authElevate"' in html and 'id="authDowngrade"' in html
assert 'data-admin-action="刷新服务器目录"' in html and 'data-admin-action="控制自动下单总开关"' in html
assert '.auth-read-role' in css and "content:'🛡'" in css
assert "state.auth.baseToken" in js and "state.auth.adminToken" in js
print('v0.21 browser step-up UI tests: OK')