from pathlib import Path
import json
root=Path(__file__).resolve().parents[1];ps=(root/'KimsufiTray.ps1').read_text(encoding='utf-8-sig');cfg=json.loads((root/'KimsufiTray.json').read_text(encoding='utf-8'));server=json.loads((root/'KimsufiServer.json').read_text(encoding='utf-8'))
assert cfg['apiTokenEnv']=='KSC_API_TOKEN';assert 'Bearer $token' in ps;assert "Invoke-CommanderGet '/api/state'" in ps;assert "Properties['authRequired']" in ps
assert server['authEnabled'] is False and server['authTokenEnv']=='KSC_API_TOKEN' and 'authToken' not in server
print('v0.20 tray auth tests: OK')