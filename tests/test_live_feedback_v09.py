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
    spec = importlib.util.spec_from_file_location("ksc09_feedback", td / "KimsufiStockCommander.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ksc09_feedback"] = mod
    spec.loader.exec_module(mod)
    return td_obj, mod, mod.Commander(td, demo=True)


def main():
    td_obj, mod, c = load_temp_commander()
    try:
        c.refresh_catalog()
        le6 = [x for x in c.catalog if x["planCode"] == "26skle06"]
        assert len(le6) == 2, le6
        assert all(x["memory"] and x["storage"] for x in le6), le6
        assert all(x["status"] == "unavailable" for x in le6), le6

        nvme = next(x for x in le6 if "nvme" in x["storage"])
        t = c.upsert_target({
            "planCode": nvme["planCode"], "name": nvme["name"],
            "memory": nvme["memory"], "storage": nvme["storage"],
            "price": nvme["price"], "watch": True, "autoOrder": False,
        })
        c.poll_target(t["id"])
        assert c.targets[t["id"]]["lastState"] == "unavailable"
        assert not c.targets[t["id"]].get("lastError")

        profiles = c.scan_payment_profiles(force=True)
        combos = {(x["duration"], x["mode"]) for x in profiles["profiles"]}
        assert {("P1M", "default"), ("P3M", "upfront3"), ("P6M", "upfront6"),
                ("P1Y", "upfront12"), ("P2Y", "upfront24")} <= combos

        c.update_settings({
            "orderDefaults": {"priceDuration": "P1Y", "priceMode": "upfront12"},
            "budgetPolicy": {"enabled": True, "currency": "EUR", "totalLimit": 1000,
                             "baselineReserved": 0, "perOrderReserveCap": 0,
                             "bufferPercent": 0, "bufferFixed": 0},
        })
        c.resolve_order_options(t["id"])
        ok, dur, mode = c.preflight_price_config(t["id"])
        assert ok and (dur, mode) == ("P1Y", "upfront12")
        est = c.refresh_checkout_estimate(t["id"], dur, mode)
        assert est["complete"]
        assert abs(est["baseAmount"] - 323.88) < 1e-6
        assert abs(est["optionAmount"] - 24.00) < 1e-6
        assert abs(est["amount"] - 347.88) < 1e-6
        budget_ok, budget = c.preflight_budget(t["id"])
        assert budget_ok and abs(budget["baseAmount"] - 347.88) < 1e-6
        assert abs(mod.simulation_budget_reserve(c.targets[t["id"]], c.settings["budgetPolicy"], "EUR") - 347.88) < 1e-6
        print("v0.9 live-feedback regression: OK")
    finally:
        td_obj.cleanup()


if __name__ == "__main__":
    main()