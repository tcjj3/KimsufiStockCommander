import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from KimsufiStockCommander import Commander

def p(amount,d="P1M",m="default"):
    return {"duration":d,"mode":m,"amount":float(amount),"currency":"EUR","text":f"{amount:.2f} EUR"}

def main():
    with tempfile.TemporaryDirectory() as td:
        r=Path(td); (r/"ui").mkdir()
        c=Commander(r,demo=True)

        pricing={
          "24sk50-v1":{
            "source":"test","prices":[p(17.99),p(205.09,"P1Y","upfront12")],
            "options":[
              {"family":"memory","planCode":"ram-32g-ecc-2400-24sk50-v1","name":"32GB DDR4 ECC 2400MHz","mandatory":True,"prices":[p(0),p(0,"P1Y","upfront12")]},
              {"family":"memory","planCode":"ram-64g-ecc-2400-24sk50-v1","name":"64GB DDR4 ECC 2400MHz","mandatory":True,"prices":[p(8),p(91.2,"P1Y","upfront12")]},
              {"family":"storage","planCode":"softraid-2x2000sa-24sk50-v1","name":"2x 2TB HDD","mandatory":True,"prices":[p(0),p(0,"P1Y","upfront12")]},
              {"family":"storage","planCode":"softraid-3x4000sa-24sk50-v1","name":"3x 4TB HDD","mandatory":True,"prices":[p(9),p(102.6,"P1Y","upfront12")]},
            ]
          },
          "27skle03-v1":{
            "source":"test","prices":[p(19.99),p(227.89,"P1Y","upfront12")],
            "options":[
              {"family":"memory","planCode":"ram-32g-ddr4-ecc-2666-27skle03-v1","name":"32GB DDR4 ECC 2666MHz","mandatory":True,"prices":[p(0),p(0,"P1Y","upfront12")]},
              {"family":"storage","planCode":"softraid-2x4000sa-27skle03-v1","name":"2x 4TB HDD","mandatory":True,"prices":[p(0),p(0,"P1Y","upfront12")]},
              {"family":"storage","planCode":"softraid-3x4000sa-27skle03-v1","name":"3x 4TB HDD","mandatory":True,"prices":[p(2),p(22.8,"P1Y","upfront12")]},
            ]
          },
          "27skle06-v1":{
            "source":"test","prices":[p(26.99),p(307.69,"P1Y","upfront12")],
            "options":[
              {"family":"memory","planCode":"ram-128g-ddr4-ecc-2400-27skle06-v1","name":"128GB DDR4 ECC 2400MHz","mandatory":True,"prices":[p(0),p(0,"P1Y","upfront12")]},
              {"family":"storage","planCode":"softraid-2x1920nvme-27skle06-v1","name":"2x 1.92TB NVMe","mandatory":True,"prices":[p(3),p(34.2,"P1Y","upfront12")]},
            ]
          }
        }
        c.public_pricing=lambda plan,force=False: pricing[plan]

        # Synthetic catalog-default metadata: importantly, 24sk50's 2x4TB is NOT present;
        # 27skle03 has only 32GB. Mirrors the live diagnostic evidence.
        metas={
          "24sk50-v1":{"families":{
            "memory":[{"value":"ram-32g-ecc-2400-24sk50","default":True},{"value":"ram-64g-ecc-2400-24sk50","default":False}],
            "storage":[{"value":"softraid-2x2000sa-24sk50","default":True},{"value":"softraid-3x4000sa-24sk50","default":False}],
          }},
          "27skle03-v1":{"families":{
            "memory":[{"value":"ram-32g-ddr4-ecc-2666-27skle03","default":True}],
            "storage":[{"value":"softraid-2x4000sa-27skle03","default":True},{"value":"softraid-3x4000sa-27skle03","default":False}],
          }},
          "27skle06-v1":{"families":{
            "memory":[{"value":"ram-128g-ddr4-ecc-2400-27skle06","default":True}],
            "storage":[{"value":"softraid-2x1920nvme-27skle06","default":False}],
          }}
        }
        c.catalog_option_metadata=lambda plan,force=False: metas[plan]

        # Live-evidence ghost #1: availability says 2x4TB on 24sk50; cart does not offer it.
        row1={"id":"a","planCode":"24sk50-v1","memory":"ram-32g-ecc-2400","storage":"softraid-2x4000sa"}
        o1=c.catalog_row_orderability(row1)
        assert o1["status"]=="availability_only" and "storage" in o1["missingFamilies"],o1

        # Live-evidence ghost #2: availability says 64GB on LE-3; cart offers only 32GB.
        row2={"id":"b","planCode":"27skle03-v1","memory":"ram-64g-ddr4-ecc-2666","storage":"softraid-3x4000sa"}
        o2=c.catalog_row_orderability(row2)
        assert o2["status"]=="availability_only" and "memory" in o2["missingFamilies"],o2

        # Known-good control.
        row3={"id":"c","planCode":"27skle06-v1","memory":"ram-128g-ddr4-ecc-2400","storage":"softraid-2x1920nvme"}
        o3=c.catalog_row_orderability(row3)
        assert o3["status"]=="orderable",o3
        est=c.estimate_catalog_row_price(row3,"P1M","default")
        assert est["complete"] and est["amount"]==29.99,est

        # Ghost price must not masquerade as a configuration price.
        c.catalog=[row1]
        estg=c.estimate_catalog_row_price(row1,"P1M","default")
        assert not estg["complete"] and estg["reason"]=="availability_only",estg

if __name__=="__main__":
    main()
    print("v0.15 orderability tests: OK")