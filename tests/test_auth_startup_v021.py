import json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def free_port():
    s=socket.socket();s.bind(('127.0.0.1',0));p=s.getsockname()[1];s.close();return p

def request(url,token=None,method='GET',payload=None):
    data=json.dumps(payload).encode() if payload is not None else None
    r=urllib.request.Request(url,data=data,method=method)
    if data:r.add_header('Content-Type','application/json')
    if token:r.add_header('Authorization','Bearer '+token)
    try:
        with urllib.request.urlopen(r,timeout=2) as x:return x.status,json.loads(x.read().decode())
    except urllib.error.HTTPError as e:return e.code,json.loads(e.read().decode())

def make_root(td):
    dst=Path(td)/'ksc';dst.mkdir();
    shutil.copy2(ROOT/'KimsufiStockCommander.py',dst/'KimsufiStockCommander.py');shutil.copytree(ROOT/'ui',dst/'ui')
    (dst/'KimsufiServer.json').write_text(json.dumps({'authEnabled':True,'readTokenEnv':'KSC_READ_TOKEN','adminTokenEnv':'KSC_ADMIN_TOKEN','authTokenEnv':'KSC_API_TOKEN'}),encoding='utf-8')
    return dst

def main():
    with tempfile.TemporaryDirectory() as td:
        root=make_root(td);env=os.environ.copy();env['LOCALAPPDATA']=str(Path(td)/'local');env.pop('KSC_READ_TOKEN',None);env.pop('KSC_ADMIN_TOKEN',None);env.pop('KSC_API_TOKEN',None)
        p=subprocess.run([sys.executable,str(root/'KimsufiStockCommander.py'),'--demo','--host','127.0.0.1','--port',str(free_port())],env=env,capture_output=True,text=True,timeout=10)
        assert p.returncode==3,(p.returncode,p.stdout,p.stderr)
    with tempfile.TemporaryDirectory() as td:
        root=make_root(td);port=free_port();token='R'*32;env=os.environ.copy();env['LOCALAPPDATA']=str(Path(td)/'local');env['KSC_READ_TOKEN']=token;env.pop('KSC_ADMIN_TOKEN',None);env.pop('KSC_API_TOKEN',None)
        proc=subprocess.Popen([sys.executable,str(root/'KimsufiStockCommander.py'),'--demo','--host','127.0.0.1','--port',str(port)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        base=f'http://127.0.0.1:{port}'
        try:
            for _ in range(50):
                try:
                    c,h=request(base+'/api/health');
                    if c==200:break
                except Exception:time.sleep(.1)
            else:raise AssertionError('read-only server did not start')
            c,a=request(base+'/api/auth/check',token);assert c==200 and a['role']=='read' and a['adminAvailable'] is False and a['authMode']=='read-only'
            c,s=request(base+'/api/state',token);assert c==200 and 'catalog' in s
            c,e=request(base+'/api/settings',token,'POST',{'country':'IE'});assert c==403 and e['error']=='admin_required' and e['adminAvailable'] is False
        finally:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
    print('v0.21 auth startup/read-only integration: OK')
if __name__=='__main__':main()