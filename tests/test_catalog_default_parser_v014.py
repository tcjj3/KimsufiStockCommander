import sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from KimsufiStockCommander import parse_catalog_options_output, Commander, CATALOG_OPTION_CACHE_SCHEMA


def test_empty_description_true_is_default():
    text = """option                         description    default\n-----------                    -----------    -------\nstorage=softraid-2x4000sa                     true\nmemory=ram-32g-ecc-2400                       false\n"""
    x = parse_catalog_options_output(text)
    assert x["storage"][0]["description"] == "", x
    assert x["storage"][0]["default"] is True, x
    assert x["memory"][0]["default"] is False, x


def test_normal_description_still_works():
    text = """option                         description               default\n-----------                    -----------               -------\nmemory=ram-64g-ecc-2666        64 GB ECC RAM 2666 MHz    true\n"""
    x = parse_catalog_options_output(text)
    assert x["memory"][0]["description"] == "64 GB ECC RAM 2666 MHz", x
    assert x["memory"][0]["default"] is True, x


def test_old_cache_is_invalidated():
    with tempfile.TemporaryDirectory() as td:
        r=Path(td); (r/'ui').mkdir(); (r/'data').mkdir()
        (r/'data'/'catalog-option-cache.json').write_text(json.dumps({
            'ovh-eu|IE|24sk50-v1': {'planCode':'24sk50-v1','families':{'storage':[{'value':'softraid-2x4000sa','description':'true','default':False}]}}
        }), encoding='utf-8')
        c=Commander(r,demo=True)
        assert c.catalog_option_cache == {}, c.catalog_option_cache


def test_new_schema_cache_kept():
    with tempfile.TemporaryDirectory() as td:
        r=Path(td); (r/'ui').mkdir(); (r/'data').mkdir()
        val={'schemaVersion':CATALOG_OPTION_CACHE_SCHEMA,'planCode':'24sk50-v1','families':{}}
        (r/'data'/'catalog-option-cache.json').write_text(json.dumps({'ovh-eu|IE|24sk50-v1':val}), encoding='utf-8')
        c=Commander(r,demo=True)
        assert c.catalog_option_cache, c.catalog_option_cache


if __name__ == '__main__':
    test_empty_description_true_is_default()
    test_normal_description_still_works()
    test_old_cache_is_invalidated()
    test_new_schema_cache_kept()
    print('v0.14 catalog default parser tests: OK')