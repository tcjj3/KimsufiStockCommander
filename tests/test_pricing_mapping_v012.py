import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from KimsufiStockCommander import Commander

def price(amount, duration="P1M", mode="default"):
    return {"duration":duration,"mode":mode,"amount":amount,"currency":"EUR","text":f"{amount:.2f} EUR"}

def main():
    with tempfile.TemporaryDirectory() as td:
        r = Path(td)
        (r/"ui").mkdir()
        c = Commander(r, demo=True)
        c.public_pricing = lambda plan, force=False: {
            "source":"test",
            "prices":[price(17.99), price(205.09,"P1Y","upfront12")],
            "options":[
                {"family":"memory","planCode":"weird-memory-24sk50","name":"32 GB ECC RAM 2400 MHz","mandatory":True,
                 "prices":[price(0),price(0,"P1Y","upfront12")]},
                {"family":"storage","planCode":"weird-disk-24sk50","name":"2 x 4 TB SATA HDD","mandatory":True,
                 "prices":[price(3),price(34.2,"P1Y","upfront12")]},
            ],
        }
        row={"id":"x","planCode":"24sk50-v1","memory":"ram-32g-ecc-2400","storage":"softraid-2x4000sa"}
        m=c.estimate_catalog_row_price(row,"P1M","default")
        y=c.estimate_catalog_row_price(row,"P1Y","upfront12")
        assert m["complete"] and m["amount"] == 20.99, m
        assert y["complete"] and y["amount"] == 239.29, y
        assert {x["matchMethod"] for x in m["options"]} == {"semantic"}

if __name__=="__main__":
    main()
    print("v0.12 pricing mapping integration: OK")