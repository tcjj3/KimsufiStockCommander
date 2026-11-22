import sys, time, threading, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from KimsufiStockCommander import Commander

with tempfile.TemporaryDirectory() as td:
    td=Path(td); (td/'ui').mkdir()
    c=Commander(td,demo=True)
    c.refresh_catalog()
    plans=sorted({r['planCode'] for r in c.catalog})
    c.refresh_public_pricing(plans, force=True)
    c.refresh_catalog_option_metadata(plans, force=True)
    c.snapshot()
    c.snapshot_cache_at=0
    orig=c._build_snapshot
    entered=threading.Event()
    def slow():
        entered.set(); time.sleep(.6); return orig()
    c._build_snapshot=slow
    out=[]
    def call(i):
        st=time.perf_counter(); c.snapshot(); out.append((i,time.perf_counter()-st))
    lead=threading.Thread(target=call,args=(0,)); lead.start(); assert entered.wait(1)
    others=[threading.Thread(target=call,args=(i,)) for i in range(1,6)]
    [x.start() for x in others]; [x.join() for x in others]; lead.join()
    assert all(dt < .15 for i,dt in out if i!=0), out
    r=c.update_settings({'notifications':{'telegramChatId':'123','telegramTokenEnv':'ABC_TOKEN'}})
    assert r['notifications']['telegramChatId']=='123'
    assert r['notifications']['telegramTokenEnv']=='ABC_TOKEN'
print('v0.17 state-pipeline regression: OK')