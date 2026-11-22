import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from KimsufiStockCommander import (
    parse_list_output, parse_check_output, parse_ram_gb, disk_types,
    humanize_storage, resolve_full_option, parse_price_output,
    parse_money_text, compute_budget_reserve, build_stock_episodes, build_watch_prediction,
    phase_capture_probability, normalize_replay_dc_strategy, build_replay_episode, choose_replay_interval,
    first_poll_hit, simulation_budget_reserve
)

LIST = """planCode          category    name                             price        status         datacenters
--------          --------    ----                             -----        ------         -----------
24ska01           Kimsufi     KS-A | Intel i7-6700k            4.99 EUR     unavailable
25skle01          Kimsufi     KS-LE-1                          9.99 EUR     available      bhs
24sk40            Kimsufi     KS-4 | Intel Xeon-E3 1230 v6     16.99 EUR    available      bhs, fra, gra
"""
CHECK = """planCode    memory                storage              status         datacenters
--------    ------                -------              ------         -----------
25skle01    ram-16g-noecc-1333    softraid-2x480ssd    unavailable
25skle01    ram-32g-noecc-1333    softraid-3x2000sa    available      bhs
"""

def main():
    plans = parse_list_output(LIST)
    assert len(plans) == 3, plans
    assert plans[2]["name"] == "KS-4 | Intel Xeon-E3 1230 v6"
    assert plans[2]["datacenters"] == ["bhs","fra","gra"]
    vars = parse_check_output(CHECK)
    assert len(vars) == 2, vars
    assert vars[1]["status"] == "available" and vars[1]["datacenters"] == ["bhs"]
    assert parse_ram_gb("ram-128g-ecc-2400") == 128
    assert disk_types("softraid-2x512nvme") == ["nvme"]
    assert disk_types("softraid-2x4000sa") == ["hdd"]
    assert humanize_storage("softraid-2x4000sa") == "2×4 TB HDD"
    out = "memory=ram-64g-ecc-2400-24sk50-v1\nstorage=softraid-2x450nvme-24sk50-v1\n"
    assert resolve_full_option(out,"memory","ram-64g-ecc-2400","24sk50-v1") == "ram-64g-ecc-2400-24sk50-v1"
    prices = parse_price_output("""price-duration    price-mode    price         description
--------------    ----------    -----         -----------
P1M               default       26.99 EUR     Monthly billing
P1Y               upfront12     323.88 EUR    12 months paid upfront
""")
    assert prices[0]["duration"] == "P1M" and prices[0]["mode"] == "default"
    assert prices[1]["duration"] == "P1Y" and prices[1]["mode"] == "upfront12"
    assert prices[1]["amount"] == 323.88 and prices[1]["currency"] == "EUR"
    assert parse_money_text("1,234.56 EUR") == (1234.56, "EUR")
    assert compute_budget_reserve(100, 10, 5, 0) == 115.0
    assert compute_budget_reserve(100, 10, 5, 150) == 150.0
    hist = [
        {"time":"2026-11-19T10:00:00+08:00","event":"SOLD_OUT","targetId":"x","datacenters":[],"pollIntervalSeconds":30},
        {"time":"2026-11-19T11:00:00+08:00","event":"AVAILABLE","targetId":"x","datacenters":["gra"],"pollIntervalSeconds":30},
        {"time":"2026-11-19T11:00:20+08:00","event":"AVAILABLE","targetId":"x","datacenters":["gra"],"pollIntervalSeconds":30},
        {"time":"2026-11-19T11:00:30+08:00","event":"DC_CHANGE","targetId":"x","datacenters":["rbx"],"pollIntervalSeconds":30},
        {"time":"2026-11-19T11:01:30+08:00","event":"SOLD_OUT","targetId":"x","datacenters":[],"pollIntervalSeconds":30},
    ]
    eps = build_stock_episodes(hist)
    assert len(eps) == 1, eps
    assert eps[0]["observedSeconds"] == 90
    assert eps[0]["datacenters"] == ["gra", "rbx"]
    assert eps[0]["availableDetections"] == 2
    assert eps[0]["uncertaintyMinusSeconds"] == 30 and eps[0]["uncertaintyPlusSeconds"] == 30
    crossed = build_stock_episodes([
        {"time":"2026-11-19T12:00:00+08:00","event":"AVAILABLE","targetId":"y","datacenters":["gra"],"pollIntervalSeconds":30},
        {"time":"2026-11-19T12:00:40+08:00","event":"SESSION_START","targetId":"","datacenters":[],"pollIntervalSeconds":30,"sessionId":"s2"},
        {"time":"2026-11-19T12:01:00+08:00","event":"SOLD_OUT","targetId":"y","datacenters":[],"pollIntervalSeconds":30},
    ])
    assert len(crossed) == 1 and crossed[0]["continuityUncertain"] is True
    assert len(crossed[0]["sessionBreaks"]) == 1

    # Prediction: repeated weekly starts around the same UTC slot should make
    # that slot materially stronger than an opposite-hour query.
    base = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    pred_eps = []
    for weeks_ago in range(8, 0, -1):
        start = base - timedelta(days=7 * weeks_ago)
        pred_eps.append({
            "startEpoch": start.timestamp(), "start": start.isoformat(),
            "closed": True, "observedSeconds": 180, "continuityUncertain": False,
            "datacenters": ["gra"],
        })
    now = base.timestamp()
    pred = build_watch_prediction(pred_eps, now_epoch=now, current_poll_seconds=30, half_life_days=30, dc_fit_score=100)
    pred_off = build_watch_prediction(pred_eps, now_epoch=now - 12 * 3600, current_poll_seconds=30, half_life_days=30, dc_fit_score=100)
    assert 0 <= pred["watchIndex"] <= 100
    assert 0 <= pred["confidence"] <= 100
    assert pred["components"]["slotActivity"] > pred_off["components"]["slotActivity"]
    assert len(pred["forecast"]) == 24 and pred["nextPeak"] is not None
    assert pred["medianGapSeconds"] == 7 * 86400
    assert pred["durationSampleCount"] == 8

    # Replay phase math: a 10-second window under a 30-second cadence is
    # capturable by one third of all possible polling phases.
    assert abs(phase_capture_probability([(0, 10)], 30) - (1/3)) < 1e-9
    assert abs(phase_capture_probability([(0, 10), (20, 25)], 30) - 0.5) < 1e-9
    assert phase_capture_probability([(0, 31)], 30) == 1.0

    ep = eps[0]
    fixed_gra = normalize_replay_dc_strategy({"mode":"fixed","fixed":"gra"}, ["gra","rbx"])
    rep = build_replay_episode(ep, fixed_gra)
    assert rep["dcEligible"] is True
    # GRA is known only for the first 30 seconds before DC_CHANGE to RBX.
    assert rep["nominalSegments"][0][1] - rep["nominalSegments"][0][0] == 30
    assert phase_capture_probability(rep["nominalSegments"], 30) == 1.0

    fixed_bhs = normalize_replay_dc_strategy({"mode":"fixed","fixed":"bhs"}, ["gra","rbx","bhs"])
    rep_bhs = build_replay_episode(ep, fixed_bhs)
    assert rep_bhs["dcEligible"] is False
    assert phase_capture_probability(rep_bhs["nominalSegments"], 30) == 0.0

    rows = [
        {"intervalSeconds":5,"callsPerDayPerTarget":17280,"nominalCaptureRatePercent":100},
        {"intervalSeconds":15,"callsPerDayPerTarget":5760,"nominalCaptureRatePercent":99},
        {"intervalSeconds":30,"callsPerDayPerTarget":2880,"nominalCaptureRatePercent":96},
        {"intervalSeconds":60,"callsPerDayPerTarget":1440,"nominalCaptureRatePercent":80},
    ]
    rec = choose_replay_interval(rows, retain_percent=95, max_calls_per_day=0)
    assert rec["intervalSeconds"] == 30, rec
    rec_limited = choose_replay_interval(rows, retain_percent=95, max_calls_per_day=2000)
    assert rec_limited["intervalSeconds"] == 60, rec_limited
    assert first_poll_hit([(100, 120)], 30, 0) == 120 or first_poll_hit([(100, 120)], 30, 0) is None
    # Endpoint uses half-open [start,end), so phase 0 polls at 90/120 and misses [100,120).
    assert first_poll_hit([(100, 120)], 30, 0) is None
    assert first_poll_hit([(100, 121)], 30, 0) == 120
    assert simulation_budget_reserve({"budgetReserveOverride":150}, {"bufferPercent":10,"bufferFixed":5}, "EUR") == 150.0
    assert simulation_budget_reserve({"resolvedPriceAmount":100,"resolvedPriceCurrency":"EUR"}, {"bufferPercent":10,"bufferFixed":5}, "EUR") == 115.0
    assert simulation_budget_reserve({"resolvedPriceAmount":100,"resolvedPriceCurrency":"USD"}, {"bufferPercent":10,"bufferFixed":5}, "EUR") is None
    print("parser/budget/radar/prediction/replay tests: OK")

if __name__ == "__main__":
    main()