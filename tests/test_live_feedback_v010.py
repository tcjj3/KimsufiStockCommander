import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_temp_commander():
    td_obj = tempfile.TemporaryDirectory()
    td = Path(td_obj.name)
    shutil.copy2(ROOT / "KimsufiStockCommander.py", td / "KimsufiStockCommander.py")
    shutil.copytree(ROOT / "ui", td / "ui")
    spec = importlib.util.spec_from_file_location("ksc010_feedback", td / "KimsufiStockCommander.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ksc010_feedback"] = mod
    spec.loader.exec_module(mod)
    return td_obj, mod, mod.Commander(td, demo=True)


def main():
    td_obj, mod, c = load_temp_commander()
    try:
        # Before the explicit configuration-price scan, /api/state-style snapshot
        # must not fetch network/CLI options and must not pretend the base plan is
        # an exact configuration total.
        c.refresh_catalog()
        snap0 = c.configuration_price_snapshot()
        le6_rows = [x for x in c.catalog if x["planCode"] == "26skle06"]
        assert len(le6_rows) == 2
        assert all(not snap0[x["id"]]["complete"] for x in le6_rows), snap0
        assert all(snap0[x["id"]]["reason"].startswith("pricing_not_cached") for x in le6_rows)

        # Explicit payment scan now also scans option prices for the selected
        # global payment profile. Same plan + different storage must be allowed
        # to produce different totals.
        c.update_settings({"orderDefaults": {"priceDuration": "P1Y", "priceMode": "upfront12"}})
        profiles = c.scan_payment_profiles(force=True)
        assert profiles["configurationScan"]["cachedPlanCount"] > 0
        snap = c.configuration_price_snapshot()
        hdd = next(x for x in le6_rows if "4000sa" in x["storage"])
        nvme = next(x for x in le6_rows if "512nvme" in x["storage"])
        ph = snap[hdd["id"]]
        pn = snap[nvme["id"]]
        assert ph["complete"] and pn["complete"], (ph, pn)
        assert abs(ph["baseAmount"] - 323.88) < 1e-6
        assert abs(ph["optionAmount"] - 12.00) < 1e-6
        assert abs(pn["optionAmount"] - 24.00) < 1e-6
        assert abs(ph["amount"] - 335.88) < 1e-6
        assert abs(pn["amount"] - 347.88) < 1e-6
        assert ph["amount"] != pn["amount"]

        # Empty catalog while worker is loading gets a clear, intentional error
        # instead of an accidental zero-plan scan/stack trace.
        c.catalog = []
        c.catalog_refreshing = True
        try:
            c.scan_payment_profiles(force=True)
            raise AssertionError("expected catalog-loading error")
        except ValueError as e:
            assert "目录正在加载" in str(e)

        print("v0.10 configuration-price/live-startup regression: OK")
    finally:
        td_obj.cleanup()


if __name__ == "__main__":
    main()