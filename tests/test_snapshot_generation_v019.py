import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from KimsufiStockCommander import Commander

with tempfile.TemporaryDirectory() as td:
    r = Path(td)
    (r / "ui").mkdir()
    c = Commander(r, demo=True)
    c.catalog = [{"id":"p|m|s","planCode":"p","name":"P","memory":"m","storage":"s","price":1.0,"currency":"EUR","datacenters":[]}]
    c.catalog_row_orderability = lambda row: {"status":"orderable","orderable":True,"reason":"ok"}
    c.estimate_catalog_row_price = lambda *a, **k: {"complete":True,"amount":1,"currency":"EUR","duration":"P1M","mode":"default","baseAmount":1,"optionAmount":0}
    c.snapshot()
    rev0 = c.snapshot_generation
    c.upsert_target({"planCode":"p","name":"P","memory":"m","storage":"s","price":1,"watch":True,"autoOrder":False,"desiredQty":1})
    assert c.snapshot_generation > rev0
    b = c.snapshot()
    assert b["targets"]["p|m|s"]["watch"] is True
    assert b["stateRevision"] == c.snapshot_generation
print("v0.19 snapshot generation regression: OK")