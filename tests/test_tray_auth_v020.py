from pathlib import Path
import json
root=Path(__file__).resolve().parents[1];ps=(root/'KimsufiTray.ps1').read_text(encoding='utf-8-sig');cfg=json.loads((root/'KimsufiTray.json').read_text(encoding='utf-8'));server=json.loads((root/'KimsufiServer.json').read_text(encoding='utf-8'))
assert cfg['apiTokenEnv']=='KSC_READ_TOKEN';assert 'Bearer $token' in ps;assert "Start-CommanderGetAsync '/api/state'" in ps;assert "Properties['authRequired']" in ps
assert '$script:AuthRole' in ps and '已连接 · Read' in ps and '已连接 · Admin' in ps
assert '需要 Admin' in ps
assert server['authEnabled'] is False and server['readTokenEnv']=='KSC_READ_TOKEN' and server['adminTokenEnv']=='KSC_ADMIN_TOKEN' and 'authToken' not in server
print('v0.21 tray role auth tests: OK')