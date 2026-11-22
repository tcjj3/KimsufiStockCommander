import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from KimsufiStockCommander import Commander,find_order_option_match

def price(a,d='P1M',m='default'):
 return {'duration':d,'mode':m,'amount':a,'currency':'EUR','text':f'{a:.2f} EUR'}

def main():
 with tempfile.TemporaryDirectory() as td:
  r=Path(td);(r/'ui').mkdir();c=Commander(r,demo=True)
  plan='27skle03-v1'
  c.public_pricing=lambda p,force=False:{'planCode':plan,'prices':[price(19.99),price(227.89,'P1Y','upfront12')],
    'options':[{'family':'storage','planCode':'disk-opt-a','name':'2 x 4 TB SATA HDD','mandatory':True,'prices':[price(0),price(0,'P1Y','upfront12')]}]}
  c.catalog_option_cache[c._catalog_option_cache_key(plan)]={'planCode':plan,'families':{
   'memory':[{'value':'ram-64g-ddr4-ecc-2666','description':'64 GB ECC 2666 MHz','default':True}],
   'storage':[{'value':'softraid-2x4000sa','description':'2 x 4 TB SATA HDD','default':True}]}}
  row={'id':'le3','planCode':plan,'memory':'ram-64g-ddr4-ecc-2666','storage':'softraid-2x4000sa'}
  e=c.estimate_catalog_row_price(row,'P1M','default')
  assert e['complete'] and e['amount']==19.99,e
  mem=[x for x in e['options'] if x['family']=='memory'][0]
  assert mem.get('includedInBase') is True,mem

 # cross-family semantic: actual family spelling is opaque but name is strong
 rows=[
  {'family':'hardware-ram-choice','planCode':'weird-a','name':'64 GB ECC RAM 2666 MHz','mandatory':True,'prices':[]},
  {'family':'disk-pack','planCode':'weird-b','name':'2 x 8 TB SATA HDD','mandatory':True,'prices':[]},
 ]
 hit,method,score,_=find_order_option_match(rows,'memory','ram-64g-ddr4-ecc-2666','',plan)
 assert hit and hit['planCode']=='weird-a',(method,score)
 hit2,method2,score2,_=find_order_option_match(rows,'storage','softraid-2x8000sa','',plan)
 assert hit2 and hit2['planCode']=='weird-b',(method2,score2)
 print('v0.13 extra default/cross-family tests: OK')
if __name__=='__main__':main()