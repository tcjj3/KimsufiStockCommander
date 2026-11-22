import json, threading, urllib.request, urllib.error, tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from KimsufiStockCommander import Handler

class FakeCommander:
    def __init__(self,root):
        self.ui=Path(root)/'ui';self.ui.mkdir(parents=True,exist_ok=True);(self.ui/'index.html').write_text('ok',encoding='utf-8');self.notification_history=[]
        self.settings={}
    def health_status(self):return {'ok':True,'secretMetric':7}
    def snapshot(self):return {'secretState':'readable'}
    def update_settings(self,p):self.settings.update(p);return dict(self.settings)
    def strategy_replay(self,p):return {'replay':True}
    def portfolio_replay(self,p):return {'portfolio':True}
    def optimize_strategies(self,p):return {'optimizer':True}

def req(base,path,token=None,method='GET',payload=None):
    data=None
    if payload is not None:data=json.dumps(payload).encode()
    r=urllib.request.Request(base+path,data=data,method=method)
    if data is not None:r.add_header('Content-Type','application/json')
    if token:r.add_header('Authorization','Bearer '+token)
    try:
        with urllib.request.urlopen(r,timeout=3) as x:return x.status,json.loads(x.read().decode())
    except urllib.error.HTTPError as e:return e.code,json.loads(e.read().decode())

def main():
    read='R'*32;admin='A'*32
    with tempfile.TemporaryDirectory() as td:
        Handler.commander=FakeCommander(td);Handler.auth_enabled=True;Handler.read_token=read;Handler.admin_token=admin;Handler.legacy_token='';Handler.auth_mode='read-admin'
        srv=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=srv.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{srv.server_address[1]}'
        try:
            c,x=req(base,'/api/auth/check',read);assert c==200 and x['role']=='read' and 'state:read' in x['capabilities'] and 'settings:write' not in x['capabilities'] and x['adminAvailable']
            c,x=req(base,'/api/auth/check',admin);assert c==200 and x['role']=='admin' and 'settings:write' in x['capabilities']
            c,x=req(base,'/api/state',read);assert c==200 and x['secretState']=='readable'
            c,x=req(base,'/api/settings',None,'POST',{'country':'IE'});assert c==401 and x['error']=='authentication_required'
            c,x=req(base,'/api/replay',read,'POST',{});assert c==200 and x['replay']
            c,x=req(base,'/api/settings',read,'POST',{'country':'IE'});assert c==403 and x['error']=='admin_required' and x['requiredCapability']=='settings:write' and x['adminAvailable']
            c,x=req(base,'/api/settings',read,'POST',{'autoOrderArmed':True});assert c==403 and x['requiredCapability']=='automation:control'
            c,x=req(base,'/api/settings',admin,'POST',{'country':'IE'});assert c==200 and x['settings']['country']=='IE'
        finally:srv.shutdown();srv.server_close()
    with tempfile.TemporaryDirectory() as td:
        Handler.commander=FakeCommander(td);Handler.auth_enabled=True;Handler.read_token=read;Handler.admin_token='';Handler.legacy_token='';Handler.auth_mode='read-only'
        srv=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=srv.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{srv.server_address[1]}'
        try:
            c,x=req(base,'/api/settings',read,'POST',{'country':'IE'});assert c==403 and x['adminAvailable'] is False
        finally:srv.shutdown();srv.server_close()
    print('v0.21 capability auth HTTP tests: OK')
if __name__=='__main__':main()