import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from KimsufiStockCommander import find_order_option_match

def test_memory_semantic_old_sku():
    rows = [
        {
            "family": "memory",
            "planCode": "ram-32g-ecc-legacy-26sk10b",
            "name": "32 GB ECC RAM 2133 MHz",
            "mandatory": True,
            "prices": [],
        },
        {
            "family": "memory",
            "planCode": "ram-64g-ecc-legacy-26sk10b",
            "name": "64 GB ECC RAM 2133 MHz",
            "mandatory": True,
            "prices": [],
        },
    ]
    hit, method, score, _ = find_order_option_match(
        rows, "memory", "ram-32g-ecc-2133", "", "26sk10b-v1"
    )
    assert hit is not None, (method, score)
    assert hit["planCode"].startswith("ram-32g-")
    assert method == "semantic"

def test_storage_semantic_old_sku():
    rows = [
        {
            "family": "storage",
            "planCode": "legacy-storage-a-24sk50",
            "name": "2 x 4 TB SATA HDD",
            "mandatory": True,
            "prices": [],
        },
        {
            "family": "storage",
            "planCode": "legacy-storage-b-24sk50",
            "name": "2 x 960 GB NVMe",
            "mandatory": True,
            "prices": [],
        },
    ]
    hit, method, score, _ = find_order_option_match(
        rows, "storage", "softraid-2x4000sa", "", "24sk50-v1"
    )
    assert hit is not None, (method, score)
    assert hit["planCode"] == "legacy-storage-a-24sk50"
    assert method == "semantic"

def test_storage_does_not_cross_capacity():
    rows = [
        {"family":"storage","planCode":"a","name":"2 x 500 GB NVMe","mandatory":True,"prices":[]},
        {"family":"storage","planCode":"b","name":"2 x 960 GB NVMe","mandatory":True,"prices":[]},
    ]
    hit, method, score, _ = find_order_option_match(
        rows, "storage", "softraid-2x960nvme", "", "whatever-v1"
    )
    assert hit is not None, (method, score)
    assert hit["planCode"] == "b"

def test_ambiguous_memory_refuses_guess():
    rows = [
        {"family":"memory","planCode":"a","name":"32 GB ECC RAM","mandatory":True,"prices":[]},
        {"family":"memory","planCode":"b","name":"32 GB ECC RAM","mandatory":True,"prices":[]},
    ]
    hit, method, score, _ = find_order_option_match(
        rows, "memory", "ram-32g-ecc-2133", "", "whatever-v1"
    )
    assert hit is None
    assert method == "semantic_ambiguous"

if __name__ == "__main__":
    test_memory_semantic_old_sku()
    test_storage_semantic_old_sku()
    test_storage_does_not_cross_capacity()
    test_ambiguous_memory_refuses_guess()
    print("v0.12 option mapping tests: OK")