from pathlib import Path
root=Path(__file__).resolve().parents[1]
js=(root/'ui'/'app.js').read_text(encoding='utf-8')
html=(root/'ui'/'index.html').read_text(encoding='utf-8')
css=(root/'ui'/'styles.css').read_text(encoding='utf-8')

assert 'storedTokenInvalid:false' in js
assert 'function hasStoredApiToken()' in js
assert "forget.hidden=!authenticated&&!stored" in js
assert "forget.textContent=authenticated?'注销并清除本地 Token':'清除已保存 Token'" in js
assert "connect.textContent=authenticated?'更换 Token':(state.auth.storedTokenInvalid?'重新认证':'连接')" in js
assert 'Token 无效，请检查后重试。' in js
assert "$('#apiTokenInput').addEventListener('input',syncAuthConnectButton)" in js
assert "$('#authTokenToggle').addEventListener('click'" in js
assert "location.protocol==='http:'" in js
assert 'id="authConnect" type="button" class="primary" disabled' in html
assert 'id="authTokenToggle"' in html
assert 'type="password"' in html
assert '#authForget[hidden]' in css
print('browser auth UX v0.20.2: OK')