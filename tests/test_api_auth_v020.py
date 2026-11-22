import json, threading, urllib.request, urllib.error, tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from KimsufiStockCommander import Handler
class FakeCommander:
    def __init__(self,root):
        self.ui=Path(root)/'ui';self.ui.mkdir(parents=True,exist_ok=True);(self.ui/'app.js').write_text('console.log("public shell")',encoding='utf-8');self.notification_history=[]
    def health_status(self):return {'ok':True,'version':'test','secretMetric':123}
    def snapshot(self):return {'secretState':'protected'}
def req(url,token=None,json_mode=True):
    r=urllib.request.Request(url)
    if token:r.add_header('Authorization','Bearer '+token)
    try:
        with urllib.request.urlopen(r,timeout=3) as x:return x.status,(json.loads(x.read().decode()) if json_mode else x.read().decode())
    except urllib.error.HTTPError as e:return e.code,json.loads(e.read().decode())
def main():
    with tempfile.TemporaryDirectory() as td:
        Handler.commander=FakeCommander(td);Handler.auth_enabled=True;Handler.auth_token='A'*32;Handler.auth_token_env='KSC_API_TOKEN'
        srv=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=srv.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{srv.server_address[1]}'
        try:
            c,h=req(base+'/api/health');assert c==200 and h['authRequired'] and not h['authenticated'] and 'secretMetric' not in h
            c,e=req(base+'/api/state');assert c==401 and e['authRequired']
            c,e=req(base+'/api/state','wrong');assert c==401
            c,a=req(base+'/api/auth/check','A'*32);assert c==200 and a['authenticated']
            c,st=req(base+'/api/state','A'*32);assert c==200 and st['secretState']=='protected'
            c,h2=req(base+'/api/health','A'*32);assert h2['secretMetric']==123 and h2['authenticated']
            c,body=req(base+'/app.js',None,False);assert c==200 and 'public shell' in body
        finally:srv.shutdown();srv.server_close()
    print('v0.20 API auth tests: OK')
if __name__=='__main__':main()