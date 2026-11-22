from pathlib import Path
import json
root=Path(__file__).resolve().parents[1];py=(root/'KimsufiStockCommander.py').read_text(encoding='utf-8');cfg=json.loads((root/'KimsufiServer.json').read_text(encoding='utf-8'))
assert 'hmac.compare_digest' in py and 'minimum 24 characters' in py and 'Handler.auth_token = auth_token if auth_enabled else ""' in py
assert cfg['authEnabled'] is False and 'authToken' not in cfg
print('v0.20 server auth config tests: OK')