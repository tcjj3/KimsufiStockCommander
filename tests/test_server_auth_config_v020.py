from pathlib import Path
import json
root=Path(__file__).resolve().parents[1];py=(root/'KimsufiStockCommander.py').read_text(encoding='utf-8');cfg=json.loads((root/'KimsufiServer.json').read_text(encoding='utf-8'))
assert 'hmac.compare_digest' in py and 'minimum 24 characters' in py
assert 'legacy-single' in py and 'KSC_READ_TOKEN' in py and 'KSC_ADMIN_TOKEN' in py and 'KSC_API_TOKEN' in py
assert cfg['authEnabled'] is False and 'authToken' not in cfg
assert cfg['readTokenEnv']=='KSC_READ_TOKEN' and cfg['adminTokenEnv']=='KSC_ADMIN_TOKEN' and cfg['authTokenEnv']=='KSC_API_TOKEN'
print('v0.20/v0.21 server auth compatibility tests: OK')