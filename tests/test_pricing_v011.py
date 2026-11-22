import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_mod():
    spec = importlib.util.spec_from_file_location('ksc011', ROOT / 'KimsufiStockCommander.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules['ksc011'] = mod
    spec.loader.exec_module(mod)
    return mod


def make_commander(mod):
    td_obj = tempfile.TemporaryDirectory()
    td = Path(td_obj.name)
    shutil.copytree(ROOT / 'ui', td / 'ui')
    return td_obj, mod.Commander(td, demo=True)


def main():
    mod = load_mod()
    td_obj, c = make_commander(mod)
    try:
        c.refresh_catalog()
        c.scan_payment_profiles(force=True)

        # Same plan, different storage -> monthly and annual configuration totals differ.
        le6 = [r for r in c.catalog if r['planCode'] == '26skle06']
        assert len(le6) == 2
        month = c.configuration_monthly_price_snapshot()
        annual = c.configuration_price_snapshot()
        hdd = next(r for r in le6 if '4000sa' in r['storage'])
        nvme = next(r for r in le6 if '512nvme' in r['storage'])
        assert month[hdd['id']]['complete'] and month[nvme['id']]['complete']
        assert month[hdd['id']]['amount'] == 27.99, month[hdd['id']]
        assert month[nvme['id']]['amount'] == 28.99, month[nvme['id']]
        assert annual[hdd['id']]['amount'] == 335.88, annual[hdd['id']]
        assert annual[nvme['id']]['amount'] == 347.88, annual[nvme['id']]

        # Explicit zero option price is valid; missing price is UNKNOWN, never zero.
        plan = '25sk602b'
        key = c._ovh_pricing_cache_key(plan)
        c.ovh_pricing_cache[key] = {
            'planCode': plan,
            'source': 'test',
            'prices': [
                {'duration':'P1M','mode':'default','amount':54.99,'currency':'EUR','price':'54.99 EUR'},
                {'duration':'P1Y','mode':'upfront12','amount':626.89,'currency':'EUR','price':'626.89 EUR'},
            ],
            'options': [
                {'family':'memory','planCode':f'ram-64g-ecc-2133-{plan}','name':'64G','mandatory':True,'prices':[
                    {'duration':'P1M','mode':'default','amount':0.0,'currency':'EUR','text':'0.00 EUR'},
                    {'duration':'P1Y','mode':'upfront12','amount':0.0,'currency':'EUR','text':'0.00 EUR'},
                ]},
                {'family':'memory','planCode':f'ram-256g-ecc-2400-{plan}','name':'256G','mandatory':True,'prices':[
                    {'duration':'P1M','mode':'default','amount':32.0,'currency':'EUR','text':'32.00 EUR'},
                    # deliberately no P1Y/upfront12 row
                ]},
                {'family':'storage','planCode':f'softraid-2x480ssd-{plan}','name':'SSD','mandatory':True,'prices':[
                    {'duration':'P1M','mode':'default','amount':0.0,'currency':'EUR','text':'0.00 EUR'},
                    {'duration':'P1Y','mode':'upfront12','amount':0.0,'currency':'EUR','text':'0.00 EUR'},
                ]},
            ],
        }
        c.price_cache[c._price_cache_key(plan)] = {'planCode':plan,'prices':c.ovh_pricing_cache[key]['prices']}
        row64 = {'id':'64','planCode':plan,'memory':'ram-64g-ecc-2133','storage':'softraid-2x480ssd'}
        row256 = {'id':'256','planCode':plan,'memory':'ram-256g-ecc-2400','storage':'softraid-2x480ssd'}
        p64 = c.estimate_catalog_row_price(row64,'P1M','default')
        p256 = c.estimate_catalog_row_price(row256,'P1M','default')
        assert p64['complete'] and abs(p64['amount'] - 54.99) < 1e-9, p64
        assert p256['complete'] and abs(p256['amount'] - 86.99) < 1e-9, p256
        y256 = c.estimate_catalog_row_price(row256,'P1Y','upfront12')
        assert not y256['complete'], y256
        assert y256['amount'] is None, y256
        assert 'option_price_missing:memory' in y256['reason'], y256

        # Currency-symbol parsing: blank must remain distinguishable at caller level.
        assert mod.parse_money_text('307.69 €') == (307.69, 'EUR')
        assert mod.parse_money_text('55,45 €') == (55.45, 'EUR')
        parsed = mod.parse_order_options_output('item-option    name    price\nmemory=foo-plan    Foo RAM    \n')
        # tabular text may omit a trailing empty column; if parsed, it must not claim a known zero price.
        if parsed:
            assert parsed[0]['amount'] is None and not parsed[0]['priceKnown'], parsed

        print('v0.11 OVH raw pricing engine tests: OK')
    finally:
        td_obj.cleanup()


if __name__ == '__main__':
    main()