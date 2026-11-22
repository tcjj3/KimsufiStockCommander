import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from KimsufiStockCommander import Commander

def price(a,d='P1M',m='default'):
    return {'duration':d,'mode':m,'amount':a,'currency':'EUR','text':f'{a:.2f} EUR'}

def main():
  with tempfile.TemporaryDirectory() as td:
    r=Path(td); (r/'ui').mkdir()
    c=Commander(r,demo=True)
    plan='24sk50-v1'
    c.public_pricing=lambda p,force=False:{
      'planCode':plan,'prices':[price(17.99),price(205.09,'P1Y','upfront12')],
      # memory upgrade exists; default storage intentionally absent from cart options
      'options':[{'family':'memory','planCode':'ram-32g-ecc-2400-x','name':'32 GB ECC 2400 MHz','mandatory':True,'prices':[price(0),price(0,'P1Y','upfront12')]}]
    }
    key=c._catalog_option_cache_key(plan)
    c.catalog_option_cache[key]={'planCode':plan,'families':{
      'memory':[{'value':'ram-32g-ecc-2400','description':'32 GB ECC 2400 MHz','default':True}],
      'storage':[{'value':'softraid-2x4000sa','description':'2 x 4 TB SATA HDD','default':True},
                 {'value':'softraid-2x960nvme','description':'2 x 960 GB NVMe','default':False}]
    }}
    row={'id':'x','planCode':plan,'memory':'ram-32g-ecc-2400','storage':'softraid-2x4000sa'}
    m=c.estimate_catalog_row_price(row,'P1M','default')
    assert m['complete'],m
    assert m['amount']==17.99,m
    st=[x for x in m['options'] if x['family']=='storage'][0]
    assert st['amount']==0 and st.get('includedInBase') is True,st

    bad={'id':'y','planCode':plan,'memory':'ram-32g-ecc-2400','storage':'softraid-2x960nvme'}
    mb=c.estimate_catalog_row_price(bad,'P1M','default')
    # Since v0.15, a configuration present in availability but absent from the
    # current orderable cart/catalog set is classified explicitly as
    # availability_only instead of a generic option_missing error.
    assert not mb['complete'] and mb['reason']=='availability_only',mb

    # resolver: default storage is satisfied without explicit item-option
    c.targets[row['id']]={'id':'x','planCode':plan,'name':'KS-5','memory':row['memory'],'storage':row['storage'],
      'orderMemoryOption':'','orderStorageOption':'','orderMemoryIncluded':False,'orderStorageIncluded':False,
      'watch':True,'autoOrder':False,'desiredQty':1,'completedQty':0}
    c.resolve_order_options('x')
    t=c.targets['x']
    assert t['orderStorageIncluded'] is True,t
    assert c._target_option_satisfied(t,'storage')
    assert t['orderStorageOption']=='',t

  print('v0.13 catalog default tests: OK')

if __name__=='__main__': main()