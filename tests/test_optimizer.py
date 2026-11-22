import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import KimsufiStockCommander as ksc


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        shutil.copytree(ROOT / "ui", root / "ui")
        c = ksc.Commander(root, demo=True)
        le6 = "26skle06|ram-128g-ecc-2400|softraid-2x512nvme"
        le5 = "26skle05|ram-64g-ecc-2400|softraid-2x450nvme"
        c.targets = {
            le6: {"id":le6,"planCode":"26skle06","name":"KS-LE-6","autoOrder":True,"watch":True,"desiredQty":1,"completedQty":0,"orderPriority":100,"budgetReserveOverride":100.0},
            le5: {"id":le5,"planCode":"26skle05","name":"KS-LE-5","autoOrder":True,"watch":True,"desiredQty":1,"completedQty":0,"orderPriority":50,"budgetReserveOverride":100.0},
        }
        ksc.atomic_write_json(c.targets_path, c.targets)
        base = datetime.now(timezone.utc) - timedelta(days=2)
        def stamp(seconds): return (base + timedelta(seconds=seconds)).isoformat()
        history = [
            {"time":stamp(0),"event":"AVAILABLE","targetId":le6,"datacenters":["gra"],"pollIntervalSeconds":30},
            {"time":stamp(90),"event":"SOLD_OUT","targetId":le6,"datacenters":["gra"],"pollIntervalSeconds":30},
            {"time":stamp(0),"event":"AVAILABLE","targetId":le5,"datacenters":["gra"],"pollIntervalSeconds":30},
            {"time":stamp(90),"event":"SOLD_OUT","targetId":le5,"datacenters":["gra"],"pollIntervalSeconds":30},
            {"time":stamp(86400),"event":"AVAILABLE","targetId":le6,"datacenters":["rbx"],"pollIntervalSeconds":30},
            {"time":stamp(86450),"event":"SOLD_OUT","targetId":le6,"datacenters":["rbx"],"pollIntervalSeconds":30},
        ]
        with c.stock_history_path.open("w", encoding="utf-8") as f:
            for row in history:
                f.write(json.dumps(row) + "\n")
        c._stock_history_cache_key = None

        before_settings = json.dumps(c.settings, sort_keys=True)
        before_targets = json.dumps(c.targets, sort_keys=True)
        result = c.optimize_strategies({
            "lookbackDays":30,
            "intervals":[15,30,60],
            "budgets":[100,200,0],
            "dcSearch":"balanced",
            "prioritySearch":"heuristic",
            "retainPercent":95,
            "maxCandidates":1000,
        })
        assert result["evaluatedCandidateCount"] > 0
        assert result["paretoCount"] > 0
        assert result["recommendation"] is not None
        assert result["model"]["mutatesLiveSettings"] is False
        assert any(x["priorityProfile"] == "当前优先级" for x in result["topCandidates"])
        assert json.dumps(c.settings, sort_keys=True) == before_settings
        assert json.dumps(c.targets, sort_keys=True) == before_targets

        capped = c.optimize_strategies({
            "lookbackDays":30,
            "intervals":[15,30,60],
            "budgets":[100,200,0],
            "dcSearch":"extended",
            "prioritySearch":"exhaustive",
            "maxCandidates":50,
        })
        assert capped["evaluatedCandidateCount"] <= 50
        assert capped["searchTruncated"] is True
        # The cap must not simply keep only the first/fastest interval prefix.
        assert capped["searchedDimensions"]["intervals"] >= 2
        print("optimizer tests: OK")


if __name__ == "__main__":
    main()