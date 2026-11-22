from pathlib import Path

root=Path(__file__).resolve().parents[1]
html=(root/'ui'/'index.html').read_text(encoding='utf-8')
css=(root/'ui'/'styles.css').read_text(encoding='utf-8')

assert 'class="auth-remember"' in html
assert 'class="auth-remember-copy"' in html
assert '推荐保存 Read Token；双 Token 模式下 Admin Token 不会写入 localStorage' in html
assert 'class="dialog-actions auth-actions"' in html
assert '.auth-remember{display:flex!important' in css
assert '.auth-remember input[type=checkbox]' in css
assert '.auth-forget{margin-left:auto}' in css
assert '@media(max-width:620px)' in css
print('browser auth layout v0.20.3: OK')