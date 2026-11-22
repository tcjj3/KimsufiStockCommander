#!/usr/bin/env python3
"""
Kimsufi Stock Commander v0.21.9
Windows-first local web dashboard for TheoBrigitte/kimsufi-notifier.

Design goals:
- Standard library only.
- Bind to 127.0.0.1 by default.
- Low-frequency full catalog discovery; high-frequency polling only for watched variants.
- Preserve conservative order semantics:
  * only "order completed:" is success;
  * a clean "datacenter ... not available" stock race is retryable;
  * every other uncertain checkout enters REVIEW and is not retried.
- Desired quantity means final desired count. Orders are submitted one at a time.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import io
import math
import statistics
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import permutations
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import uuid

APP_VERSION = "0.21.9"
CATALOG_OPTION_CACHE_SCHEMA = 2
DEFAULT_PORT = 8765
KNOWN_DCS = ["bhs", "fra", "gra", "hil", "lon", "par", "rbx", "sbg", "sgp", "syd", "vin", "waw", "ynm", "yyz"]
OVH_API_BASES = {
    "ovh-eu": "https://eu.api.ovh.com/1.0",
    "ovh-ca": "https://ca.api.ovh.com/1.0",
    "ovh-us": "https://api.us.ovhcloud.com/1.0",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "exe": str(Path.home() / "go" / "bin" / "kimsufi-notifier.exe"),
    "endpoint": "ovh-eu",
    "country": "IE",
    "category": "kimsufi",
    "pollIntervalSeconds": 30,
    "catalogRefreshSeconds": 900,
    "catalogWorkers": 4,
    "autoOrderArmed": False,
    "orderDefaults": {
        "priceDuration": "P1Y",
        "priceMode": "upfront12",
        "autoPay": True,
        "catalogMonthlyPriceCap": 30.0,
    },
    "budgetPolicy": {
        "enabled": False,
        "currency": "EUR",
        "totalLimit": 0.0,
        "baselineReserved": 0.0,
        "perOrderReserveCap": 0.0,
        "bufferPercent": 10.0,
        "bufferFixed": 0.0,
        "requireFiniteForAutoPay": True,
    },
    "notifications": {
        "enabled": True,
        "desktop": True,
        "telegramEnabled": False,
        "telegramChatId": "",
        "telegramTokenEnv": "KSC_TELEGRAM_BOT_TOKEN",
        "heartbeatHours": 6.0,
        "events": [
            "AVAILABLE", "DC_CHANGE", "ORDER_SUCCESS", "ORDER_REVIEW",
            "BUDGET_BLOCKED", "PRICE_BLOCKED", "CREDENTIALS_MISSING"
        ],
        "silentEvents": ["HEARTBEAT"],
    },
    "dcStrategy": {
        "mode": "priority",
        "fixed": "gra",
        "selected": ["gra", "rbx", "sbg"],
        "priority": KNOWN_DCS,
        "fallback": "wait",
    },
}

DEMO_LIST_OUTPUT = """planCode          category    name                             price        status         datacenters
--------          --------    ----                             -----        ------         -----------
26skle01          Kimsufi     KS-LE-1                          13.99 EUR    available      gra, rbx
26skle02          Kimsufi     KS-LE-2                          15.99 EUR    available      sbg
26skle03          Kimsufi     KS-LE-3                          19.99 EUR    available      waw, bhs
26skle04          Kimsufi     KS-LE-4                          21.99 EUR    unavailable
26skle05          Kimsufi     KS-LE-5                          24.99 EUR    available      gra, fra
26skle06          Kimsufi     KS-LE-6                          26.99 EUR    unavailable
"""

DEMO_CHECKS = {
    "26skle01": "26skle01    ram-16g-ecc-1333     softraid-2x480ssd      available      gra, rbx\n",
    "26skle02": "26skle02    ram-32g-ecc-1333     softraid-2x480ssd      available      sbg\n",
    "26skle03": "26skle03    ram-32g-ecc-2666     softraid-2x4000sa      available      waw, bhs\n",
    "26skle04": "26skle04    ram-32g-ecc-2400     softraid-2x4000sa      unavailable\n26skle04    ram-32g-ecc-2400     softraid-2x450nvme    unavailable\n",
    "26skle05": "26skle05    ram-64g-ecc-2400     softraid-2x450nvme    available      gra, fra\n",
    "26skle06": "26skle06    ram-128g-ecc-2400    softraid-2x4000sa     unavailable\n26skle06    ram-128g-ecc-2400    softraid-2x512nvme   unavailable\n",
}

DEMO_PRICES = {
    plan: "price-duration    price-mode    price         description\n"
          "--------------    ----------    -----         -----------\n"
          + ("P1M               default       26.99 EUR     Monthly billing\n" if plan == "26skle06" else "P1M               default       24.99 EUR     Monthly billing\n")
          + ("P3M               upfront3      79.49 EUR     3 months paid upfront\n" if plan in {"26skle05", "26skle06"} else "")
          + ("P6M               upfront6      156.99 EUR    6 months paid upfront\n" if plan == "26skle06" else "")
          + ("P1Y               upfront12     299.88 EUR    12 months paid upfront\n" if plan == "26skle05" else ("P1Y               upfront12     323.88 EUR    12 months paid upfront\n" if plan == "26skle06" else "P1Y               upfront12     287.88 EUR    12 months paid upfront\n"))
          + ("P2Y               upfront24     575.76 EUR    24 months paid upfront\n" if plan == "26skle06" else "")
    for plan in DEMO_CHECKS
}



def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def durable_write_text(path: Path, text: str) -> None:
    """Write a small safety marker/receipt and fsync it before returning."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(obj, ensure_ascii=False, indent=2)
    with tmp.open("w", encoding="utf-8", newline="") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        broken = path.with_suffix(path.suffix + ".broken")
        try:
            path.replace(broken)
        except Exception:
            pass
        return default


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(base))
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class ExclusiveFileLock:
    """Cross-process advisory lock held on one byte."""

    def __init__(self, path: Path, nonblocking: bool = False):
        self.path = path
        self.nonblocking = nonblocking
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a+b")
        if self.file.tell() == 0:
            self.file.write(b"\0")
            self.file.flush()
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            mode = msvcrt.LK_NBLCK if self.nonblocking else msvcrt.LK_LOCK
            try:
                msvcrt.locking(self.file.fileno(), mode, 1)
            except OSError:
                self.file.close()
                self.file = None
                raise
        else:
            import fcntl
            flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if self.nonblocking else 0)
            try:
                fcntl.flock(self.file.fileno(), flags)
            except OSError:
                self.file.close()
                self.file = None
                raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.file:
            return
        try:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None


@dataclass
class CommandResult:
    code: int
    output: str
    args: list[str]


class Commander:
    def __init__(self, root: Path, demo: bool = False):
        self.root = root
        self.data = root / "data"
        self.ui = root / "ui"
        self.data.mkdir(exist_ok=True)
        (self.data / "locks").mkdir(exist_ok=True)
        (self.data / "receipts").mkdir(exist_ok=True)
        self.settings_path = self.data / "settings.json"
        self.targets_path = self.data / "targets.json"
        self.catalog_path = self.data / "catalog-cache.json"
        self.events_path = self.data / "events.csv"
        self.orders_path = self.data / "orders.log"
        self.error_path = self.data / "errors.log"
        self.budget_ledger_path = self.data / "budget-ledger.json"
        self.notification_history_path = self.data / "notification-history.json"
        self.stock_history_path = self.data / "stock-history.jsonl"
        self.price_cache_path = self.data / "price-cache.json"
        self.config_price_cache_path = self.data / "config-price-cache.json"
        self.ovh_pricing_cache_path = self.data / "ovh-pricing-cache.json"
        self.catalog_option_cache_path = self.data / "catalog-option-cache.json"

        self.demo = demo
        self.settings = deep_merge(DEFAULT_SETTINGS, load_json(self.settings_path, {}))
        self.targets: dict[str, dict[str, Any]] = load_json(self.targets_path, {})
        self.catalog: list[dict[str, Any]] = load_json(self.catalog_path, [])
        self.price_cache: dict[str, dict[str, Any]] = load_json(self.price_cache_path, {})
        if not isinstance(self.price_cache, dict):
            self.price_cache = {}
        self.config_price_cache: dict[str, dict[str, Any]] = load_json(self.config_price_cache_path, {})
        if not isinstance(self.config_price_cache, dict):
            self.config_price_cache = {}
        self.ovh_pricing_cache: dict[str, dict[str, Any]] = load_json(self.ovh_pricing_cache_path, {})
        if not isinstance(self.ovh_pricing_cache, dict):
            self.ovh_pricing_cache = {}
        self.catalog_option_cache: dict[str, dict[str, Any]] = load_json(self.catalog_option_cache_path, {})
        if not isinstance(self.catalog_option_cache, dict):
            self.catalog_option_cache = {}
        else:
            # v0.13 and earlier could parse an empty description as the literal
            # string "true", turning catalog defaults into False.  Never reuse
            # those stale cache entries after the parser fix.
            self.catalog_option_cache = {
                k: v for k, v in self.catalog_option_cache.items()
                if isinstance(v, dict) and int(v.get("schemaVersion", 0) or 0) == CATALOG_OPTION_CACHE_SCHEMA
            }
        self.budget_ledger: list[dict[str, Any]] = load_json(self.budget_ledger_path, [])
        if not isinstance(self.budget_ledger, list):
            self.budget_ledger = []
        self.notification_history: list[dict[str, Any]] = load_json(self.notification_history_path, [])
        if not isinstance(self.notification_history, list):
            self.notification_history = []
        self.notification_history = self.notification_history[-200:]
        self.notification_last_error: str | None = None
        self.stock_history_lock = threading.Lock()
        self._stock_history_cache_key: tuple[int, int] | None = None
        self._stock_history_cache: list[dict[str, Any]] = []
        self.started_at = now_iso()
        self.catalog_updated_at: str | None = None

        self.state_lock = threading.RLock()
        # /api/state is relatively expensive because it derives orderability and
        # configuration pricing for the whole catalog.  Keep that work off the
        # mutable-state lock and never let concurrent browser polls queue a train
        # of identical snapshot builds.  While one build is in progress, callers
        # receive the last completed snapshot immediately (stale-while-revalidate).
        self.snapshot_build_lock = threading.Lock()
        self.snapshot_cache: dict[str, Any] | None = None
        self.snapshot_cache_at = 0.0
        self.snapshot_last_build_ms = 0.0
        self.snapshot_building = False
        # Monotonic logical revision for UI-visible mutable state. Mutating APIs
        # increment it so an in-flight snapshot built from older target/settings
        # data can never overwrite the cache after a successful save.
        self.snapshot_generation = 0
        self.snapshot_cache_generation = -1
        self.order_lock = threading.Lock()
        self.cli_semaphore = threading.Semaphore(4)
        self.stop_event = threading.Event()
        self.catalog_refresh_event = threading.Event()
        self.force_poll_event = threading.Event()
        self.catalog_refreshing = False
        self.last_poll_at: str | None = None
        self.last_catalog_error: str | None = None
        self.worker_thread: threading.Thread | None = None

        self._reconcile_persistent_safety_state()
        self._reconcile_budget_ledger()
        self._bootstrap_stock_history()
        self._append_monitor_session_start()
        self._persist_all()

    # ---------- persistence / safety ----------

    def _target_key(self, plan: str, memory: str, storage: str) -> str:
        return f"{plan}|{memory}|{storage}"

    def _target_hash(self, target_id: str) -> str:
        return hashlib.sha256(target_id.encode("utf-8")).hexdigest()[:16]

    def _review_lock_path(self, target_id: str) -> Path:
        return self.data / "locks" / f"{self._target_hash(target_id)}.review.lock"

    def _active_order_path(self, target_id: str) -> Path:
        return self.data / "locks" / f"{self._target_hash(target_id)}.active.order"

    def _receipt_glob(self, target_id: str) -> list[Path]:
        h = self._target_hash(target_id)
        return sorted((self.data / "receipts").glob(f"{h}-success-*.txt"))

    def _reconcile_persistent_safety_state(self) -> None:
        for tid, target in self.targets.items():
            target.setdefault("orderPriority", 50)
            target.setdefault("budgetReserveOverride", 0.0)
            target.setdefault("orderMemoryIncluded", False)
            target.setdefault("orderStorageIncluded", False)
            if "inheritPrice" not in target:
                target["inheritPrice"] = (
                    str(target.get("priceDuration", "")) == str(self.settings["orderDefaults"].get("priceDuration", ""))
                    and str(target.get("priceMode", "")) == str(self.settings["orderDefaults"].get("priceMode", ""))
                )
            receipts = self._receipt_glob(tid)
            target["completedQty"] = max(int(target.get("completedQty", 0)), len(receipts))
            review_path = self._review_lock_path(tid)
            active_path = self._active_order_path(tid)
            if active_path.exists() and not review_path.exists():
                durable_write_text(
                    review_path,
                    "REVIEW REQUIRED\n"
                    f"Recovered at={now_iso()}\n"
                    "An active-order marker survived a previous process. "
                    "The previous checkout result may be ambiguous.\n",
                )
            if review_path.exists():
                target["review"] = True
                try:
                    target["reviewReason"] = review_path.read_text(encoding="utf-8")[:4000]
                except Exception:
                    target["reviewReason"] = "Persistent review lock exists."

    def _reconcile_budget_ledger(self) -> None:
        """Rebuild missing budget entries from v0.3+ success receipts."""
        known = {str(x.get("receipt", "")) for x in self.budget_ledger if isinstance(x, dict)}
        changed = False
        for receipt in sorted((self.data / "receipts").glob("*-success-*.txt")):
            if receipt.name in known:
                continue
            try:
                fields = {}
                for line in receipt.read_text(encoding="utf-8", errors="replace").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        fields[k.strip()] = v.strip()
                if "BudgetReserve" not in fields or "Currency" not in fields:
                    continue
                entry = {
                    "receipt": receipt.name,
                    "time": fields.get("Time", ""),
                    "targetId": fields.get("Target", ""),
                    "currency": fields.get("Currency", ""),
                    "baseAmount": float(fields.get("BasePrice", "0") or 0),
                    "reservedAmount": float(fields.get("BudgetReserve", "0") or 0),
                    "priceDuration": fields.get("PriceDuration", ""),
                    "priceMode": fields.get("PriceMode", ""),
                }
                self.budget_ledger.append(entry)
                known.add(receipt.name)
                changed = True
            except Exception as e:
                self.log_error(f"budget receipt reconcile {receipt.name}: {e}")
        if changed:
            atomic_write_json(self.budget_ledger_path, self.budget_ledger)

    def budget_summary(self) -> dict[str, Any]:
        policy = self.settings.get("budgetPolicy", {})
        currency = str(policy.get("currency", "EUR") or "EUR").upper()
        baseline = max(0.0, float(policy.get("baselineReserved", 0) or 0))
        ledger_reserved = sum(
            max(0.0, float(x.get("reservedAmount", 0) or 0))
            for x in self.budget_ledger
            if str(x.get("currency", "")).upper() == currency
        )
        foreign = sum(
            1 for x in self.budget_ledger
            if x.get("currency") and str(x.get("currency")).upper() != currency
        )
        used = round(baseline + ledger_reserved, 2)
        limit = max(0.0, float(policy.get("totalLimit", 0) or 0))
        remaining = None if limit <= 0 else round(max(0.0, limit - used), 2)
        return {
            "enabled": bool(policy.get("enabled")),
            "currency": currency,
            "totalLimit": limit,
            "baselineReserved": round(baseline, 2),
            "ledgerReserved": round(ledger_reserved, 2),
            "usedReserved": used,
            "remaining": remaining,
            "foreignEntryCount": foreign,
            "entries": list(reversed(self.budget_ledger[-20:])),
        }

    def _append_budget_entry(
        self, receipt_name: str, target_id: str, base_amount: float,
        reserved_amount: float, currency: str, price_duration: str, price_mode: str
    ) -> None:
        if any(str(x.get("receipt", "")) == receipt_name for x in self.budget_ledger):
            return
        self.budget_ledger.append({
            "receipt": receipt_name,
            "time": now_iso(),
            "targetId": target_id,
            "currency": currency,
            "baseAmount": round(float(base_amount), 2),
            "reservedAmount": round(float(reserved_amount), 2),
            "priceDuration": price_duration,
            "priceMode": price_mode,
        })
        atomic_write_json(self.budget_ledger_path, self.budget_ledger)

    def _bootstrap_stock_history(self) -> None:
        """Reconcile legacy events.csv into the v0.5 stock-history stream.

        This runs on every start, but only appends stock transitions that are
        missing from stock-history.jsonl. It also repairs the narrow crash
        window where events.csv was flushed but the JSONL append was not.
        """
        if not self.events_path.exists():
            return
        known: set[str] = set()
        if self.stock_history_path.exists():
            try:
                with self.stock_history_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        event = str(obj.get("event", "")).upper()
                        if event in {"AVAILABLE", "SOLD_OUT", "DC_CHANGE"}:
                            known.add(_stock_record_key(obj))
            except Exception as e:
                self.log_error(f"stock history reconcile read: {e}")

        missing: list[dict[str, Any]] = []
        try:
            with self.events_path.open("r", newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    event = str(row.get("Event", "")).strip().upper()
                    if event not in {"AVAILABLE", "SOLD_OUT", "DC_CHANGE"}:
                        continue
                    rec = {
                        "time": str(row.get("Timestamp", "")).strip(),
                        "event": event,
                        "targetId": str(row.get("TargetId", "")).strip(),
                        "datacenters": split_dcs(str(row.get("Datacenters", ""))),
                        "note": str(row.get("Note", "")).strip(),
                        "pollIntervalSeconds": None,
                        "source": "events.csv-reconcile",
                    }
                    key = _stock_record_key(rec)
                    if key not in known:
                        known.add(key)
                        missing.append(rec)
        except Exception as e:
            self.log_error(f"stock history reconcile: {e}")
            return
        if not missing:
            return
        with self.stock_history_lock:
            with self.stock_history_path.open("a", encoding="utf-8") as f:
                for rec in missing:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._stock_history_cache_key = None
            self._stock_history_cache = []

    def _append_monitor_session_start(self) -> None:
        rec = {
            "time": self.started_at,
            "event": "SESSION_START",
            "targetId": "",
            "datacenters": [],
            "note": "Watcher process started; continuity across the preceding offline interval is unobserved.",
            "pollIntervalSeconds": int(self.settings.get("pollIntervalSeconds", 30) or 30),
            "source": "v0.5",
            "sessionId": uuid.uuid4().hex,
        }
        try:
            with self.stock_history_lock:
                with self.stock_history_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self._stock_history_cache_key = None
        except Exception as e:
            self.log_error(f"stock session marker: {e}")

    def _append_stock_observation(
        self, timestamp: str, event: str, target_id: str, dcs: str, note: str
    ) -> None:
        if event not in {"AVAILABLE", "SOLD_OUT", "DC_CHANGE"} or not target_id:
            return
        rec = {
            "time": timestamp,
            "event": event,
            "targetId": target_id,
            "datacenters": split_dcs(dcs),
            "note": note,
            "pollIntervalSeconds": int(self.settings.get("pollIntervalSeconds", 30) or 30),
            "source": "v0.5",
        }
        try:
            with self.stock_history_lock:
                with self.stock_history_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self._stock_history_cache_key = None
        except Exception as e:
            self.log_error(f"stock history append: {e}")

    def _read_stock_history(self) -> list[dict[str, Any]]:
        if not self.stock_history_path.exists():
            return []
        try:
            st = self.stock_history_path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return []
        with self.stock_history_lock:
            if self._stock_history_cache_key == key:
                return [dict(x) for x in self._stock_history_cache]
            out: list[dict[str, Any]] = []
            try:
                with self.stock_history_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if str(obj.get("event", "")).upper() not in {"AVAILABLE", "SOLD_OUT", "DC_CHANGE", "SESSION_START"}:
                            continue
                        out.append(obj)
            except Exception as e:
                self.log_error(f"stock history read: {e}")
                return []
            out.sort(key=lambda x: _safe_event_epoch(x.get("time")))
            self._stock_history_cache_key = key
            self._stock_history_cache = out
            return [dict(x) for x in out]

    def _analytics_target_label(self, target_id: str) -> str:
        with self.state_lock:
            t = dict(self.targets.get(target_id) or {})
        if not t:
            return target_id
        parts = [str(t.get("name") or t.get("planCode") or target_id)]
        if t.get("memory"):
            parts.append(humanize_memory(str(t.get("memory"))))
        if t.get("storage"):
            parts.append(humanize_storage(str(t.get("storage"))))
        return " · ".join(x for x in parts if x and x != "—")

    def _prediction_dc_fit(self, episodes: list[dict[str, Any]], now_epoch: float, half_life_days: float) -> float:
        weighted_total = 0.0
        weighted_fit = 0.0
        for ep in episodes:
            dcs = list(ep.get("datacenters", []))
            if not dcs:
                continue
            start_epoch = float(ep.get("startEpoch", 0) or 0)
            age_days = max(0.0, (now_epoch - start_epoch) / 86400.0)
            weight = math.exp(-math.log(2.0) * age_days / max(1.0, half_life_days))
            weighted_total += weight
            dc, _ = self.choose_datacenter(dcs)
            if dc:
                weighted_fit += weight
        if weighted_total <= 0:
            return 50.0
        return round(100.0 * weighted_fit / weighted_total, 1)

    def stock_predictions(
        self, episodes: list[dict[str, Any]], lookback_days: int = 90, now_epoch: float | None = None
    ) -> list[dict[str, Any]]:
        """Build an empirical, explainable Watch Index for each historical target.

        This is deliberately not a probability model. It ranks how attractive the
        current/future time slot is for watching based on observed restock timing,
        cadence, datacenter-policy fit and capture margin versus the polling period.
        """
        now_epoch = float(now_epoch if now_epoch is not None else time.time())
        cutoff = None if lookback_days <= 0 else now_epoch - lookback_days * 86400
        target_ids = sorted({str(e.get("targetId", "")) for e in episodes if e.get("targetId")})
        poll = max(1, int(self.settings.get("pollIntervalSeconds", 30) or 30))
        half_life_days = 30.0 if lookback_days <= 0 else max(3.0, min(60.0, lookback_days / 2.0))
        out: list[dict[str, Any]] = []

        for tid in target_ids:
            target_eps = [
                e for e in episodes
                if e.get("targetId") == tid
                and (cutoff is None or float(e.get("startEpoch", 0) or 0) >= cutoff)
            ]
            if not target_eps:
                continue
            target_eps.sort(key=lambda e: float(e.get("startEpoch", 0) or 0))
            dc_fit = self._prediction_dc_fit(target_eps, now_epoch, half_life_days)
            pred = build_watch_prediction(
                target_eps, now_epoch=now_epoch, current_poll_seconds=poll,
                half_life_days=half_life_days, dc_fit_score=dc_fit, horizon_hours=24
            )
            pred["targetId"] = tid
            pred["label"] = self._analytics_target_label(tid)
            out.append(pred)

        out.sort(key=lambda x: (-float(x.get("watchIndex", 0) or 0), -float(x.get("confidence", 0) or 0), str(x.get("targetId", ""))))
        for i, row in enumerate(out, start=1):
            row["rank"] = i
        return out

    def stock_analytics(self, target_id: str = "", lookback_days: int = 30) -> dict[str, Any]:
        records = self._read_stock_history()
        episodes = build_stock_episodes(records)
        now_epoch = time.time()
        cutoff = None if lookback_days <= 0 else now_epoch - lookback_days * 86400

        all_target_ids = sorted({str(e.get("targetId", "")) for e in episodes if e.get("targetId")})
        if target_id:
            episodes_for_target = [e for e in episodes if e.get("targetId") == target_id]
        else:
            episodes_for_target = episodes

        period = [e for e in episodes_for_target if cutoff is None or float(e.get("startEpoch", 0) or 0) >= cutoff]
        open_now = [e for e in episodes_for_target if not e.get("closed")]
        closed = [e for e in period if e.get("closed") and e.get("observedSeconds") is not None]
        duration_eligible = [e for e in closed if not e.get("continuityUncertain")]
        durations = [float(e["observedSeconds"]) for e in duration_eligible]

        dc_counts: dict[str, int] = {}
        weekday_counts = [0] * 7
        hour_counts = [0] * 24
        heatmap = [[0] * 24 for _ in range(7)]
        for ep in period:
            for dc in ep.get("datacenters", []):
                dc_counts[dc] = dc_counts.get(dc, 0) + 1
            dt = _parse_event_datetime(ep.get("start"))
            if dt is not None:
                wd = dt.weekday()
                hr = dt.hour
                weekday_counts[wd] += 1
                hour_counts[hr] += 1
                heatmap[wd][hr] += 1

        most_active_dc = ""
        if dc_counts:
            most_active_dc = sorted(dc_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        hottest_slot = None
        best = 0
        for wd in range(7):
            for hr in range(24):
                if heatmap[wd][hr] > best:
                    best = heatmap[wd][hr]
                    hottest_slot = {"weekday": wd, "hour": hr, "count": best}

        latest_seen = max((str(e.get("start", "")) for e in period), default="")
        latest_sold = max((str(e.get("end", "")) for e in closed if e.get("end")), default="")
        recent = sorted(period + [e for e in open_now if e not in period], key=lambda e: float(e.get("startEpoch", 0)), reverse=True)[:60]
        for ep in recent:
            if not ep.get("closed"):
                ep["currentObservedSeconds"] = max(0, int(now_epoch - float(ep.get("startEpoch", now_epoch))))

        target_rows = []
        for tid in all_target_ids:
            count = sum(1 for e in episodes if e.get("targetId") == tid)
            target_rows.append({"id": tid, "label": self._analytics_target_label(tid), "episodeCount": count})

        predictions = self.stock_predictions(episodes, lookback_days=lookback_days, now_epoch=now_epoch)
        selected_prediction = next((x for x in predictions if x.get("targetId") == target_id), None) if target_id else (predictions[0] if predictions else None)

        summary = {
            "episodeCount": len(period),
            "closedEpisodeCount": len(closed),
            "durationEligibleCount": len(duration_eligible),
            "continuityUncertainClosedCount": sum(1 for e in closed if e.get("continuityUncertain")),
            "currentlyOpenCount": len(open_now),
            "totalObservedSeconds": int(sum(durations)),
            "averageObservedSeconds": round(sum(durations) / len(durations), 1) if durations else None,
            "medianObservedSeconds": round(statistics.median(durations), 1) if durations else None,
            "shortestObservedSeconds": int(min(durations)) if durations else None,
            "longestObservedSeconds": int(max(durations)) if durations else None,
            "mostActiveDatacenter": most_active_dc,
            "hottestSlot": hottest_slot,
            "latestAvailableAt": latest_seen or None,
            "latestSoldOutAt": latest_sold or None,
        }
        return {
            "targetId": target_id,
            "lookbackDays": lookback_days,
            "generatedAt": now_iso(),
            "summary": summary,
            "datacenterCounts": dict(sorted(dc_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "weekdayCounts": weekday_counts,
            "hourCounts": hour_counts,
            "heatmap": heatmap,
            "recentEpisodes": recent,
            "targets": target_rows,
            "predictions": predictions,
            "selectedPrediction": selected_prediction,
            "predictionClock": "UTC",
            "predictionModel": {
                "name": "watch-index-v1",
                "horizonHours": 24,
                "weights": {"slotActivity": 0.40, "cadenceDue": 0.30, "dcFit": 0.15, "captureMargin": 0.15},
                "orderIndependent": True,
            },
            "currentPollIntervalSeconds": int(self.settings.get("pollIntervalSeconds", 30) or 30),
            "notes": {
                "duration": "Observed detection-to-detection span; closed windows only are used for duration statistics.",
                "uncertainty": "v0.5+ records polling interval per boundary. Legacy backfilled events may not have historical interval metadata.",
                "prediction": "Watch Index is an empirical ranking, not a probability. Prediction clock uses UTC and never triggers orders.",
            },
        }

    def strategy_replay(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Replay observed stock windows against hypothetical polling/DC strategies.

        The replay is intentionally observational: it only evaluates restock windows
        that were actually observed in history. It cannot infer windows that the
        historical watcher never saw, so faster-than-historical polling is not
        presented as proof that more *unknown* restocks would have been found.
        """
        target_id = str(payload.get("targetId", "")).strip()
        if not target_id:
            raise ValueError("targetId is required for replay")
        try:
            lookback_days = max(0, min(3650, int(payload.get("lookbackDays", 30))))
        except Exception:
            lookback_days = 30
        raw_intervals = payload.get("intervals", [5, 10, 15, 30, 60, 120])
        if not isinstance(raw_intervals, list):
            raw_intervals = [raw_intervals]
        intervals = sorted({max(1, min(3600, int(x))) for x in raw_intervals if str(x).strip()})
        if not intervals:
            intervals = [30]
        retain_percent = max(50.0, min(100.0, float(payload.get("retainPercent", 95) or 95)))
        max_calls_day = max(0, int(payload.get("maxCallsPerDay", 0) or 0))

        scenario = payload.get("dcStrategy")
        if not isinstance(scenario, dict) or str(scenario.get("mode", "current")) == "current":
            with self.state_lock:
                scenario = json.loads(json.dumps(self.settings.get("dcStrategy", {})))
            scenario["source"] = "current"
        else:
            scenario = normalize_replay_dc_strategy(scenario, KNOWN_DCS)
            scenario["source"] = "custom"

        now_epoch = time.time()
        cutoff = None if lookback_days <= 0 else now_epoch - lookback_days * 86400
        episodes = [
            e for e in build_stock_episodes(self._read_stock_history())
            if e.get("targetId") == target_id
            and e.get("closed")
            and e.get("observedSeconds") is not None
            and (cutoff is None or float(e.get("startEpoch", 0) or 0) >= cutoff)
        ]
        episodes.sort(key=lambda e: float(e.get("startEpoch", 0) or 0))

        usable: list[dict[str, Any]] = []
        skipped_continuity = 0
        dc_unknown = 0
        for ep in episodes:
            if ep.get("continuityUncertain"):
                skipped_continuity += 1
                continue
            replay_ep = build_replay_episode(ep, scenario)
            if replay_ep.get("dcUnknown"):
                dc_unknown += 1
                if str(scenario.get("mode", "any")) != "any":
                    continue
            usable.append(replay_ep)

        rows: list[dict[str, Any]] = []
        for interval in intervals:
            nominal_probs = []
            lower_probs = []
            upper_probs = []
            bound_known = 0
            dc_eligible = 0
            detail = []
            for ep in usable:
                eligible = bool(ep.get("dcEligible"))
                if eligible:
                    dc_eligible += 1
                nominal = phase_capture_probability(ep.get("nominalSegments", []) if eligible else [], interval)
                nominal_probs.append(nominal)
                lower = upper = None
                if ep.get("boundsKnown"):
                    bound_known += 1
                    lower = phase_capture_probability(ep.get("lowerSegments", []) if eligible else [], interval)
                    upper = phase_capture_probability(ep.get("upperSegments", []) if eligible else [], interval)
                    lower_probs.append(lower)
                    upper_probs.append(upper)
                detail.append({
                    "start": ep.get("start"),
                    "end": ep.get("end"),
                    "observedSeconds": ep.get("observedSeconds"),
                    "datacenters": ep.get("datacenters", []),
                    "dcEligible": eligible,
                    "dcUnknown": ep.get("dcUnknown", False),
                    "nominalCapturePercent": round(100.0 * nominal, 1),
                    "conservativeCapturePercent": round(100.0 * lower, 1) if lower is not None else None,
                    "optimisticCapturePercent": round(100.0 * upper, 1) if upper is not None else None,
                    "startPollSeconds": ep.get("startPollSeconds"),
                    "endPollSeconds": ep.get("endPollSeconds"),
                })

            denom = len(usable)
            nominal_rate = 100.0 * sum(nominal_probs) / denom if denom else 0.0
            conservative_rate = 100.0 * sum(lower_probs) / bound_known if bound_known else None
            optimistic_rate = 100.0 * sum(upper_probs) / bound_known if bound_known else None
            calls_day = int(math.ceil(86400 / interval))
            rows.append({
                "intervalSeconds": interval,
                "callsPerDayPerTarget": calls_day,
                "comparableEpisodeCount": denom,
                "dcEligibleEpisodeCount": dc_eligible,
                "dcEligibleRatePercent": round(100.0 * dc_eligible / denom, 1) if denom else 0.0,
                "nominalExpectedCaptureCount": round(sum(nominal_probs), 2),
                "nominalCaptureRatePercent": round(nominal_rate, 1),
                "conservativeCaptureRatePercent": round(conservative_rate, 1) if conservative_rate is not None else None,
                "optimisticCaptureRatePercent": round(optimistic_rate, 1) if optimistic_rate is not None else None,
                "boundKnownEpisodeCount": bound_known,
                "boundCoveragePercent": round(100.0 * bound_known / denom, 1) if denom else 0.0,
                "details": detail,
                "recommended": False,
            })

        recommendation = choose_replay_interval(rows, retain_percent=retain_percent, max_calls_per_day=max_calls_day)
        if recommendation:
            for row in rows:
                row["recommended"] = row["intervalSeconds"] == recommendation["intervalSeconds"]

        best_rate = max((float(r.get("nominalCaptureRatePercent", 0) or 0) for r in rows), default=0.0)
        return {
            "generatedAt": now_iso(),
            "targetId": target_id,
            "targetLabel": self._analytics_target_label(target_id),
            "lookbackDays": lookback_days,
            "dcStrategy": scenario,
            "episodeCountRaw": len(episodes),
            "comparableEpisodeCount": len(usable),
            "skippedContinuityUncertain": skipped_continuity,
            "dcUnknownExcluded": dc_unknown if str(scenario.get("mode", "any")) != "any" else 0,
            "retainPercent": retain_percent,
            "maxCallsPerDay": max_calls_day,
            "bestNominalCaptureRatePercent": round(best_rate, 1),
            "recommendation": recommendation,
            "rows": rows,
            "model": {
                "name": "strategy-replay-v1",
                "phaseModel": "analytic-uniform-phase",
                "dcTimelineAware": True,
                "boundaryModel": "conservative=trim sold-out boundary; optimistic=extend available-start boundary",
                "orderIndependent": True,
            },
            "notes": {
                "observedOnly": "Replay only evaluates restock windows already observed by the historical watcher; it cannot recover unobserved restocks.",
                "fasterPolling": "A faster hypothetical interval can improve phase capture of known windows, but does not prove historical missed windows would have been found.",
                "dcUnknown": "For DC-specific strategies, historical episodes without DC evidence are excluded rather than guessed.",
                "bounds": "Conservative/optimistic rates use v0.5+ boundary polling metadata when available; nominal rate uses the detected window itself.",
            },
        }

    def portfolio_replay(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Replay current auto-order targets together using their priority/quantity.

        This simulation uses nominal observed stock/DC windows and exact integer
        polling phases. It never calls the order CLI. Budget reserve amounts are
        taken only from already-known target state; when a finite simulated budget
        is requested, targets without a defensible reserve amount are not guessed.
        """
        interval = max(1, min(3600, int(payload.get("intervalSeconds", self.settings.get("pollIntervalSeconds", 30)) or 30)))
        lookback_days = max(0, min(3650, int(payload.get("lookbackDays", 30) or 30)))
        budget_input = payload.get("budget", None)
        if budget_input is None or str(budget_input).strip() == "":
            summary = self.budget_summary()
            budget = float(summary.get("remaining") or 0) if summary.get("enabled") and summary.get("remaining") is not None else 0.0
            budget_source = "current_remaining" if budget > 0 else "unlimited"
        else:
            budget = max(0.0, float(budget_input or 0))
            budget_source = "custom" if budget > 0 else "unlimited"

        with self.state_lock:
            strategy = json.loads(json.dumps(self.settings.get("dcStrategy", {})))
            target_states = {tid: dict(t) for tid, t in self.targets.items() if t.get("autoOrder")}
            budget_policy = dict(self.settings.get("budgetPolicy", {}))
        strategy["source"] = "current"
        currency = str(budget_policy.get("currency", "EUR") or "EUR").upper()

        now_epoch = time.time()
        cutoff = None if lookback_days <= 0 else now_epoch - lookback_days * 86400
        all_eps = build_stock_episodes(self._read_stock_history())
        episodes_by_target: dict[str, list[dict[str, Any]]] = {}
        for tid in target_states:
            eps = [
                e for e in all_eps
                if e.get("targetId") == tid and e.get("closed") and not e.get("continuityUncertain")
                and (cutoff is None or float(e.get("startEpoch", 0) or 0) >= cutoff)
            ]
            episodes_by_target[tid] = eps

        sim_targets: dict[str, dict[str, Any]] = {}
        skipped_no_history = []
        for tid, t in target_states.items():
            if not episodes_by_target.get(tid):
                skipped_no_history.append(tid)
                continue
            reserve = simulation_budget_reserve(t, budget_policy, currency)
            sim_targets[tid] = {
                "id": tid,
                "label": self._analytics_target_label(tid),
                "priority": max(0, min(100, int(t.get("orderPriority", 50)))),
                "desiredQty": max(1, min(50, int(t.get("desiredQty", 1)))),
                "reserveAmount": reserve,
            }

        target_acc = {
            tid: {"purchases": 0.0, "phasesWithPurchase": 0, "budgetBlocked": 0.0, "quotaBlocked": 0.0,
                  "capturedOpportunities": 0.0, "reserveUnknownBlocked": 0.0}
            for tid in sim_targets
        }
        portfolio_purchases = 0.0
        portfolio_budget_used = 0.0
        portfolio_captured = 0.0
        same_cycle_groups = 0.0
        phase_count = interval  # exact integer-second phase sweep

        # Build nominal eligible segments once; DC_CHANGE is preserved.
        replay_eps: dict[str, list[dict[str, Any]]] = {}
        for tid, eps in episodes_by_target.items():
            if tid not in sim_targets:
                continue
            replay_eps[tid] = [build_replay_episode(ep, strategy) for ep in eps]

        for phase in range(phase_count):
            opportunities = []
            for tid, eps in replay_eps.items():
                for idx, ep in enumerate(eps):
                    if not ep.get("dcEligible"):
                        continue
                    hit = first_poll_hit(ep.get("nominalSegments", []), interval, phase)
                    if hit is not None:
                        opportunities.append((hit, -sim_targets[tid]["priority"], tid, idx))
            opportunities.sort()
            acquired = {tid: 0 for tid in sim_targets}
            any_purchase = {tid: False for tid in sim_targets}
            remaining = budget
            used = 0.0
            purchases_this_phase = 0
            captured_this_phase = 0
            i = 0
            while i < len(opportunities):
                hit_time = opportunities[i][0]
                group = []
                while i < len(opportunities) and abs(opportunities[i][0] - hit_time) < 1e-6:
                    group.append(opportunities[i]); i += 1
                if len(group) > 1:
                    same_cycle_groups += 1
                # Already sorted by -priority, then target id.
                for _, _, tid, _ in group:
                    captured_this_phase += 1
                    target_acc[tid]["capturedOpportunities"] += 1
                    target = sim_targets[tid]
                    if acquired[tid] >= target["desiredQty"]:
                        target_acc[tid]["quotaBlocked"] += 1
                        continue
                    reserve = target.get("reserveAmount")
                    if budget > 0:
                        if reserve is None:
                            target_acc[tid]["reserveUnknownBlocked"] += 1
                            continue
                        if float(reserve) > remaining + 1e-9:
                            target_acc[tid]["budgetBlocked"] += 1
                            continue
                    acquired[tid] += 1
                    purchases_this_phase += 1
                    target_acc[tid]["purchases"] += 1
                    any_purchase[tid] = True
                    if budget > 0 and reserve is not None:
                        remaining -= float(reserve)
                        used += float(reserve)
            for tid, yes in any_purchase.items():
                if yes:
                    target_acc[tid]["phasesWithPurchase"] += 1
            portfolio_purchases += purchases_this_phase
            portfolio_budget_used += used
            portfolio_captured += captured_this_phase

        rows = []
        for tid, target in sorted(sim_targets.items(), key=lambda kv: (-kv[1]["priority"], kv[0])):
            a = target_acc[tid]
            rows.append({
                **target,
                "historicalEpisodeCount": len(replay_eps.get(tid, [])),
                "averagePurchases": round(a["purchases"] / phase_count, 3),
                "purchasePhaseRatePercent": round(100.0 * a["phasesWithPurchase"] / phase_count, 1),
                "averageCapturedOpportunities": round(a["capturedOpportunities"] / phase_count, 3),
                "averageBudgetBlocked": round(a["budgetBlocked"] / phase_count, 3),
                "averageQuotaBlocked": round(a["quotaBlocked"] / phase_count, 3),
                "averageReserveUnknownBlocked": round(a["reserveUnknownBlocked"] / phase_count, 3),
            })

        return {
            "generatedAt": now_iso(),
            "intervalSeconds": interval,
            "phaseCount": phase_count,
            "lookbackDays": lookback_days,
            "budget": budget,
            "budgetSource": budget_source,
            "currency": currency,
            "dcStrategy": strategy,
            "autoOrderTargetCount": len(target_states),
            "simulatedTargetCount": len(sim_targets),
            "skippedNoHistory": skipped_no_history,
            "averagePortfolioPurchases": round(portfolio_purchases / phase_count, 3) if phase_count else 0.0,
            "averagePortfolioBudgetUsed": round(portfolio_budget_used / phase_count, 2) if phase_count else 0.0,
            "averageCapturedOpportunities": round(portfolio_captured / phase_count, 3) if phase_count else 0.0,
            "averageSameCycleCompetitionGroups": round(same_cycle_groups / phase_count, 3) if phase_count else 0.0,
            "targets": rows,
            "model": {
                "name": "portfolio-replay-v1",
                "phaseModel": "exact-integer-second-phase-sweep",
                "windowModel": "nominal-observed",
                "usesCurrentPriorities": True,
                "usesCurrentDesiredQty": True,
                "usesCurrentDcStrategy": True,
                "orderIndependent": True,
            },
            "notes": {
                "priority": "Priority affects opportunities landing in the same polling instant; earlier historical opportunities can still consume budget before a later higher-priority target appears.",
                "budget": "With a finite simulated budget, a target needs a known reserve amount (override, prior budget preflight, or resolved price amount). Unknown reserve is blocked rather than guessed.",
                "desiredQty": "Replay starts each phase from zero simulated purchases and uses the current desiredQty as the scenario quota; completedQty is intentionally ignored.",
                "observedOnly": "Only historically observed windows are replayed; unobserved past restocks cannot be reconstructed.",
            },
        }

    def optimize_strategies(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Search replay-only polling/DC/priority/budget strategy combinations.

        The optimizer is intentionally isolated from live ordering. It reads
        historical stock windows and current target preferences, but never
        mutates settings/targets and never calls the order CLI.
        """
        lookback_days = max(0, min(3650, int(payload.get("lookbackDays", 30) or 30)))
        raw_intervals = payload.get("intervals", [5, 10, 15, 30, 60, 120])
        if not isinstance(raw_intervals, list):
            raw_intervals = [raw_intervals]
        intervals = sorted({max(1, min(3600, int(x))) for x in raw_intervals if str(x).strip()}) or [30]
        retain_percent = max(50.0, min(100.0, float(payload.get("retainPercent", 95) or 95)))
        max_candidates = max(50, min(20000, int(payload.get("maxCandidates", 5000) or 5000)))
        dc_search = str(payload.get("dcSearch", "balanced") or "balanced").lower()
        if dc_search not in {"current", "balanced", "extended"}:
            dc_search = "balanced"
        priority_search = str(payload.get("prioritySearch", "heuristic") or "heuristic").lower()
        if priority_search not in {"current", "heuristic", "exhaustive"}:
            priority_search = "heuristic"

        with self.state_lock:
            current_strategy = json.loads(json.dumps(self.settings.get("dcStrategy", {})))
            target_states = {tid: dict(t) for tid, t in self.targets.items() if t.get("autoOrder")}
            budget_policy = dict(self.settings.get("budgetPolicy", {}))
        currency = str(budget_policy.get("currency", "EUR") or "EUR").upper()
        if not target_states:
            raise ValueError("No current auto-order targets are configured.")

        now_epoch = time.time()
        cutoff = None if lookback_days <= 0 else now_epoch - lookback_days * 86400
        all_eps = build_stock_episodes(self._read_stock_history())
        episodes_by_target: dict[str, list[dict[str, Any]]] = {}
        for tid in target_states:
            episodes_by_target[tid] = [
                e for e in all_eps
                if e.get("targetId") == tid and e.get("closed") and not e.get("continuityUncertain")
                and (cutoff is None or float(e.get("startEpoch", 0) or 0) >= cutoff)
            ]

        sim_targets: dict[str, dict[str, Any]] = {}
        skipped_no_history: list[str] = []
        for tid, t in target_states.items():
            eps = episodes_by_target.get(tid, [])
            if not eps:
                skipped_no_history.append(tid)
                continue
            reserve = simulation_budget_reserve(t, budget_policy, currency)
            sim_targets[tid] = {
                "id": tid,
                "label": self._analytics_target_label(tid),
                "preferencePriority": max(0, min(100, int(t.get("orderPriority", 50)))),
                "desiredQty": max(1, min(50, int(t.get("desiredQty", 1)))),
                "reserveAmount": reserve,
                "historicalEpisodeCount": len(eps),
            }
        if not sim_targets:
            raise ValueError("No auto-order targets have comparable historical windows in this range.")

        # DC candidates are derived only from observed history/current policy.
        dc_counts: dict[str, int] = {}
        for tid in sim_targets:
            for ep in episodes_by_target.get(tid, []):
                for dc in ep.get("datacenters", []) or []:
                    key = str(dc).lower()
                    dc_counts[key] = dc_counts.get(key, 0) + 1
        observed_dcs = [x for x, _ in sorted(dc_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        dc_candidates: list[tuple[str, dict[str, Any]]] = []
        seen_dc: set[str] = set()
        def add_dc(label: str, strategy: dict[str, Any]) -> None:
            norm = normalize_replay_dc_strategy(strategy, KNOWN_DCS)
            sig = json.dumps(norm, sort_keys=True, separators=(",", ":"))
            if sig not in seen_dc:
                seen_dc.add(sig); dc_candidates.append((label, norm))
        add_dc("当前策略", current_strategy)
        if dc_search != "current":
            add_dc("任意机房", {"mode": "any", "selected": [], "priority": KNOWN_DCS, "fallback": "wait"})
            for dc in observed_dcs[:10]:
                add_dc(f"固定 {dc.upper()}", {"mode": "fixed", "fixed": dc, "selected": [], "priority": KNOWN_DCS, "fallback": "wait"})
            selected = [str(x).lower() for x in current_strategy.get("selected", [])]
            if selected:
                add_dc("当前集合白名单", {"mode": "whitelist", "selected": selected, "priority": current_strategy.get("priority", KNOWN_DCS), "fallback": "wait"})
                add_dc("当前集合优先+等待", {"mode": "priority", "selected": selected, "priority": current_strategy.get("priority", KNOWN_DCS), "fallback": "wait"})
                add_dc("当前集合优先+回退", {"mode": "priority", "selected": selected, "priority": current_strategy.get("priority", KNOWN_DCS), "fallback": "any"})
            if dc_search == "extended":
                for dc in observed_dcs[:8]:
                    add_dc(f"排除 {dc.upper()}", {"mode": "exclude", "selected": [dc], "priority": KNOWN_DCS, "fallback": "wait"})

        # Budget candidates: custom values or data-derived breakpoints.
        raw_budgets = payload.get("budgets", None)
        budgets: list[float] = []
        if isinstance(raw_budgets, list):
            for x in raw_budgets:
                try: budgets.append(max(0.0, round(float(x), 2)))
                except Exception: pass
        if not budgets:
            budgets = [0.0]  # unlimited
            summary = self.budget_summary()
            if summary.get("enabled") and summary.get("remaining") is not None and float(summary.get("remaining") or 0) > 0:
                budgets.append(round(float(summary["remaining"]), 2))
            unit_reserves: list[float] = []
            for t in sim_targets.values():
                if t.get("reserveAmount") is not None:
                    unit_reserves.extend([float(t["reserveAmount"])] * min(int(t["desiredQty"]), 6))
            unit_reserves.sort()
            running = 0.0
            for value in unit_reserves[:8]:
                running += value
                budgets.append(round(running, 2))
        budgets = sorted(set(budgets), key=lambda x: (x == 0.0, x))
        if len(budgets) > 10:
            # Keep representative lower/middle/upper breakpoints plus unlimited.
            finite = [x for x in budgets if x > 0]
            pick = {finite[0], finite[-1]} if finite else set()
            for frac in (0.25, 0.5, 0.75):
                if finite: pick.add(finite[min(len(finite)-1, int(round((len(finite)-1)*frac)))])
            budgets = sorted(pick) + ([0.0] if 0.0 in budgets else [])

        # Candidate execution priority profiles. Objective weights remain the
        # user's current priorities, so search cannot redefine user preference.
        tids = list(sim_targets)
        def profile_from_order(name: str, order: list[str]) -> dict[str, Any]:
            values = {}
            for i, tid in enumerate(order):
                values[tid] = max(0, 100 - i * (100 // max(1, len(order))))
            return {"name": name, "order": order, "priorities": values}
        priority_profiles: list[dict[str, Any]] = []
        seen_orders: set[tuple[str, ...]] = set()
        def add_profile(name: str, order: list[str], exact: dict[str, int] | None = None) -> None:
            key = tuple(order)
            if key in seen_orders: return
            seen_orders.add(key)
            if exact is None:
                priority_profiles.append(profile_from_order(name, order))
            else:
                priority_profiles.append({"name": name, "order": order, "priorities": dict(exact)})
        current_order = sorted(tids, key=lambda tid: (-sim_targets[tid]["preferencePriority"], tid))
        add_profile("当前优先级", current_order, {tid: sim_targets[tid]["preferencePriority"] for tid in tids})
        if priority_search != "current":
            add_profile("等优先级", sorted(tids), {tid: 50 for tid in tids})
            known_first = sorted(tids, key=lambda tid: (sim_targets[tid]["reserveAmount"] is None, float(sim_targets[tid]["reserveAmount"] or 1e30), tid))
            add_profile("低预算优先", known_first)
            add_profile("高预算优先", list(reversed(known_first)))
            scarce = sorted(tids, key=lambda tid: (sim_targets[tid]["historicalEpisodeCount"], tid))
            add_profile("稀缺目标优先", scarce)
            add_profile("高频目标优先", list(reversed(scarce)))
        if priority_search == "exhaustive" and len(tids) <= 5:
            for perm in permutations(sorted(tids)):
                add_profile("穷举 " + " > ".join(sim_targets[x]["label"] for x in perm), list(perm))

        # Replay episode cache per DC strategy.
        replay_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        def replay_eps_for(strategy: dict[str, Any], tid: str) -> list[dict[str, Any]]:
            sig = json.dumps(strategy, sort_keys=True, separators=(",", ":"))
            key = (sig, tid)
            if key not in replay_cache:
                replay_cache[key] = [build_replay_episode(ep, strategy) for ep in episodes_by_target.get(tid, [])]
            return replay_cache[key]

        def simulate(interval: int, strategy: dict[str, Any], priorities: dict[str, int], budget: float) -> dict[str, Any]:
            phase_count = interval
            acc = {tid: {"purchases": 0.0, "budgetBlocked": 0.0, "captured": 0.0} for tid in sim_targets}
            total_purchases = total_budget_used = total_captured = 0.0
            same_cycle = 0.0
            replay_eps = {tid: replay_eps_for(strategy, tid) for tid in sim_targets}
            for phase in range(phase_count):
                opportunities = []
                for tid, eps in replay_eps.items():
                    for idx, ep in enumerate(eps):
                        if not ep.get("dcEligible"): continue
                        hit = first_poll_hit(ep.get("nominalSegments", []), interval, phase)
                        if hit is not None:
                            opportunities.append((hit, -int(priorities.get(tid, 50)), tid, idx))
                opportunities.sort()
                acquired = {tid: 0 for tid in sim_targets}
                remaining = budget
                used = purchases = captured = 0.0
                i = 0
                while i < len(opportunities):
                    hit_time = opportunities[i][0]
                    group = []
                    while i < len(opportunities) and abs(opportunities[i][0] - hit_time) < 1e-6:
                        group.append(opportunities[i]); i += 1
                    if len(group) > 1: same_cycle += 1
                    for _, _, tid, _ in group:
                        captured += 1; acc[tid]["captured"] += 1
                        target = sim_targets[tid]
                        if acquired[tid] >= target["desiredQty"]: continue
                        reserve = target.get("reserveAmount")
                        if budget > 0:
                            if reserve is None or float(reserve) > remaining + 1e-9:
                                acc[tid]["budgetBlocked"] += 1; continue
                        acquired[tid] += 1; purchases += 1; acc[tid]["purchases"] += 1
                        if budget > 0 and reserve is not None:
                            remaining -= float(reserve); used += float(reserve)
                total_purchases += purchases; total_budget_used += used; total_captured += captured
            avg_purchases = total_purchases / phase_count if phase_count else 0.0
            weights_total = 0.0; weighted = 0.0
            per_target = []
            for tid, t in sim_targets.items():
                avg = acc[tid]["purchases"] / phase_count if phase_count else 0.0
                fulfill = min(1.0, avg / max(1, t["desiredQty"]))
                w = max(1.0, float(t["preferencePriority"] or 0))
                weights_total += w; weighted += fulfill * w
                per_target.append({"id": tid, "label": t["label"], "averagePurchases": round(avg, 3), "fulfillmentPercent": round(fulfill*100, 1)})
            calls_target = int(math.ceil(86400 / interval))
            calls_total = calls_target * len(sim_targets)
            return {
                "averagePortfolioPurchases": round(avg_purchases, 3),
                "weightedFulfillmentPercent": round(100.0 * weighted / weights_total, 2) if weights_total else 0.0,
                "averageBudgetUsed": round(total_budget_used / phase_count, 2) if phase_count else 0.0,
                "averageCapturedOpportunities": round(total_captured / phase_count, 3) if phase_count else 0.0,
                "averageSameCycleCompetitionGroups": round(same_cycle / phase_count, 3) if phase_count else 0.0,
                "callsPerDayPerTarget": calls_target,
                "callsPerDayTotal": calls_total,
                "targets": per_target,
            }

        total_theoretical = len(intervals) * len(dc_candidates) * len(priority_profiles) * len(budgets)
        truncated = total_theoretical > max_candidates

        # Bound combinatorics without prefix bias. Preserve all polling intervals
        # whenever possible; thin the usually-largest priority dimension first,
        # then DC and budget dimensions. Current/heuristic profiles are at the
        # front, while any exhaustive tail is sampled evenly.
        search_profiles = list(priority_profiles)
        search_dcs = list(dc_candidates)
        search_budgets = list(budgets)
        def thin_even(items: list[Any], keep: int, preserve_head: int = 1) -> list[Any]:
            if keep >= len(items): return list(items)
            keep = max(1, keep); head = list(items[:min(preserve_head, keep)])
            remaining = items[len(head):]
            slots = keep - len(head)
            if slots <= 0 or not remaining: return head
            if slots >= len(remaining): return head + list(remaining)
            idxs = sorted({min(len(remaining)-1, int(round(i*(len(remaining)-1)/max(1, slots-1)))) for i in range(slots)})
            out = head + [remaining[i] for i in idxs]
            # rounding can deduplicate indexes; top up deterministically.
            for item in remaining:
                if len(out) >= keep: break
                if item not in out: out.append(item)
            return out[:keep]

        base_other = max(1, len(intervals) * len(search_dcs) * len(search_budgets))
        if base_other * len(search_profiles) > max_candidates:
            search_profiles = thin_even(search_profiles, max(1, max_candidates // base_other), preserve_head=min(6, len(search_profiles)))
        base_other = max(1, len(intervals) * len(search_profiles) * len(search_budgets))
        if base_other * len(search_dcs) > max_candidates:
            search_dcs = thin_even(search_dcs, max(1, max_candidates // base_other), preserve_head=min(2, len(search_dcs)))
        base_other = max(1, len(intervals) * len(search_profiles) * len(search_dcs))
        if base_other * len(search_budgets) > max_candidates:
            search_budgets = thin_even(search_budgets, max(1, max_candidates // base_other), preserve_head=1)
        if len(intervals) * len(search_profiles) * len(search_dcs) * len(search_budgets) > max_candidates:
            keep_intervals = max(1, max_candidates // max(1, len(search_profiles)*len(search_dcs)*len(search_budgets)))
            search_intervals = thin_even(intervals, keep_intervals, preserve_head=1)
        else:
            search_intervals = list(intervals)

        candidates: list[dict[str, Any]] = []
        evaluated_count = 0
        for interval in search_intervals:
            for dc_label, strategy in search_dcs:
                for profile in search_profiles:
                    for budget in search_budgets:
                        if evaluated_count >= max_candidates: break
                        metrics = simulate(interval, strategy, profile["priorities"], budget)
                        evaluated_count += 1
                        candidates.append({
                            "id": evaluated_count,
                            "intervalSeconds": interval,
                            "dcLabel": dc_label,
                            "dcStrategy": strategy,
                            "priorityProfile": profile["name"],
                            "priorityOrder": profile["order"],
                            "priorities": profile["priorities"],
                            "budget": budget,
                            "budgetLabel": "不限" if budget <= 0 else f"{currency} {budget:.2f}",
                            "budgetExposure": None if budget <= 0 else budget,
                            **metrics,
                        })
                    if evaluated_count >= max_candidates: break
                if evaluated_count >= max_candidates: break
            if evaluated_count >= max_candidates: break

        # Remove exact metric duplicates while preferring a finite/lower budget exposure.
        dedup: dict[tuple[Any, ...], dict[str, Any]] = {}
        for c in candidates:
            key = (
                c["intervalSeconds"], c["dcLabel"], c["priorityProfile"],
                c["weightedFulfillmentPercent"], c["averagePortfolioPurchases"],
                c["callsPerDayTotal"], c["averageBudgetUsed"],
            )
            old = dedup.get(key)
            exposure = float("inf") if c["budgetExposure"] is None else float(c["budgetExposure"])
            old_exposure = float("inf") if old and old["budgetExposure"] is None else float(old["budgetExposure"]) if old else float("inf")
            if old is None or exposure < old_exposure:
                dedup[key] = c
        candidates = list(dedup.values())

        def dominates(a: dict[str, Any], b: dict[str, Any]) -> bool:
            ae = float("inf") if a.get("budgetExposure") is None else float(a["budgetExposure"])
            be = float("inf") if b.get("budgetExposure") is None else float(b["budgetExposure"])
            no_worse = (
                a["weightedFulfillmentPercent"] + 1e-9 >= b["weightedFulfillmentPercent"]
                and a["averagePortfolioPurchases"] + 1e-9 >= b["averagePortfolioPurchases"]
                and a["callsPerDayTotal"] <= b["callsPerDayTotal"]
                and a["averageBudgetUsed"] <= b["averageBudgetUsed"] + 1e-9
                and ae <= be + 1e-9
            )
            strict = (
                a["weightedFulfillmentPercent"] > b["weightedFulfillmentPercent"] + 1e-9
                or a["averagePortfolioPurchases"] > b["averagePortfolioPurchases"] + 1e-9
                or a["callsPerDayTotal"] < b["callsPerDayTotal"]
                or a["averageBudgetUsed"] < b["averageBudgetUsed"] - 1e-9
                or ae < be - 1e-9
            )
            return no_worse and strict

        pareto = []
        for i, c in enumerate(candidates):
            if not any(i != j and dominates(other, c) for j, other in enumerate(candidates)):
                pareto.append(c)
        pareto.sort(key=lambda c: (-c["weightedFulfillmentPercent"], c["callsPerDayTotal"], c["averageBudgetUsed"], -c["averagePortfolioPurchases"]))
        # Collapse outcome-identical Pareto points; prefer current live strategy/profile
        # when two different labels produce the same objective vector.
        unique_front: dict[tuple[Any, ...], dict[str, Any]] = {}
        for c in pareto:
            exposure = float("inf") if c.get("budgetExposure") is None else float(c["budgetExposure"])
            key = (c["weightedFulfillmentPercent"], c["averagePortfolioPurchases"], c["callsPerDayTotal"], c["averageBudgetUsed"], exposure)
            oldc = unique_front.get(key)
            rank = (0 if c["dcLabel"] == "当前策略" else 1, 0 if c["priorityProfile"] == "当前优先级" else 1)
            oldrank = (0 if oldc and oldc["dcLabel"] == "当前策略" else 1, 0 if oldc and oldc["priorityProfile"] == "当前优先级" else 1)
            if oldc is None or rank < oldrank:
                unique_front[key] = c
        pareto = sorted(unique_front.values(), key=lambda c: (-c["weightedFulfillmentPercent"], c["callsPerDayTotal"], c["averageBudgetUsed"], -c["averagePortfolioPurchases"]))

        best_weighted = max((c["weightedFulfillmentPercent"] for c in candidates), default=0.0)
        threshold = best_weighted * retain_percent / 100.0
        eligible = [c for c in pareto if c["weightedFulfillmentPercent"] + 1e-9 >= threshold]
        recommended = None
        if eligible:
            recommended = min(
                eligible,
                key=lambda c: (
                    c["callsPerDayTotal"],
                    c["averageBudgetUsed"],
                    float("inf") if c.get("budgetExposure") is None else float(c["budgetExposure"]),
                    -c["averagePortfolioPurchases"],
                ),
            )
            recommended = dict(recommended)
            recommended["retainPercent"] = retain_percent
            recommended["bestWeightedFulfillmentPercent"] = round(best_weighted, 2)
            recommended["thresholdWeightedFulfillmentPercent"] = round(threshold, 2)

        # Return only a bounded ranked subset of dominated candidates; complete
        # Pareto front is always returned.
        top = sorted(candidates, key=lambda c: (-c["weightedFulfillmentPercent"], c["callsPerDayTotal"], c["averageBudgetUsed"]))[:100]
        pareto_ids = {c["id"] for c in pareto}
        for c in top: c["pareto"] = c["id"] in pareto_ids
        for c in pareto: c["pareto"] = True

        return {
            "generatedAt": now_iso(),
            "lookbackDays": lookback_days,
            "intervals": intervals,
            "dcSearch": dc_search,
            "prioritySearch": priority_search,
            "currency": currency,
            "autoOrderTargetCount": len(target_states),
            "simulatedTargetCount": len(sim_targets),
            "skippedNoHistory": skipped_no_history,
            "dcCandidateCount": len(dc_candidates),
            "priorityProfileCount": len(priority_profiles),
            "budgetCandidates": budgets,
            "theoreticalCandidateCount": total_theoretical,
            "evaluatedCandidateCount": evaluated_count,
            "distinctCandidateCount": len(candidates),
            "maxCandidates": max_candidates,
            "searchTruncated": truncated,
            "searchedDimensions": {
                "intervals": len(search_intervals), "dcStrategies": len(search_dcs),
                "priorityProfiles": len(search_profiles), "budgets": len(search_budgets),
            },
            "paretoCount": len(pareto),
            "bestWeightedFulfillmentPercent": round(best_weighted, 2),
            "retainPercent": retain_percent,
            "recommendation": recommended,
            "pareto": pareto[:200],
            "topCandidates": top,
            "model": {
                "name": "strategy-optimizer-v1",
                "phaseModel": "exact-integer-second-phase-sweep",
                "objectives": [
                    "maximize weighted fulfillment using current priorities as preference weights",
                    "maximize average portfolio purchases",
                    "minimize total polling API calls/day",
                    "minimize average budget used",
                    "minimize finite budget exposure (unlimited treated as infinite exposure)",
                ],
                "orderIndependent": True,
                "mutatesLiveSettings": False,
            },
            "notes": {
                "pareto": "A Pareto strategy is not strictly worse than another tested strategy on every objective.",
                "recommendation": f"Recommended point is the lowest-call Pareto strategy retaining at least {retain_percent:.1f}% of the best weighted fulfillment, then lower budget use/exposure.",
                "preferenceWeights": "Current target priorities remain the objective preference weights even while candidate execution priority orders are searched.",
                "observedOnly": "Optimizer only replays historically observed windows; it cannot reconstruct restocks the historical watcher never observed.",
                "safety": "Optimizer is replay-only and never changes live settings or calls checkout.",
            },
        }

    def export_stock_analytics_csv(self, target_id: str = "", lookback_days: int = 30) -> str:
        episodes = build_stock_episodes(self._read_stock_history())
        now_epoch = time.time()
        cutoff = None if lookback_days <= 0 else now_epoch - lookback_days * 86400
        selected = [
            e for e in episodes
            if (not target_id or e.get("targetId") == target_id)
            and (cutoff is None or float(e.get("startEpoch", 0) or 0) >= cutoff or not e.get("closed"))
        ]
        selected.sort(key=lambda e: float(e.get("startEpoch", 0) or 0), reverse=True)
        buf = io.StringIO(newline="")
        fields = [
            "TargetId", "Target", "Start", "End", "Closed", "ObservedSeconds",
            "CurrentObservedSeconds", "Datacenters", "StartPollSeconds", "EndPollSeconds",
            "UncertaintyMinusSeconds", "UncertaintyPlusSeconds", "ContinuityUncertain", "SessionBreaks", "Transitions"
        ]
        w = csv.DictWriter(buf, fieldnames=fields)
        w.writeheader()
        for ep in selected:
            current_seconds = "" if ep.get("closed") else max(0, int(now_epoch - float(ep.get("startEpoch", now_epoch))))
            w.writerow({
                "TargetId": ep.get("targetId", ""),
                "Target": self._analytics_target_label(str(ep.get("targetId", ""))),
                "Start": ep.get("start", ""),
                "End": ep.get("end", ""),
                "Closed": bool(ep.get("closed")),
                "ObservedSeconds": ep.get("observedSeconds", ""),
                "CurrentObservedSeconds": current_seconds,
                "Datacenters": ",".join(ep.get("datacenters", [])),
                "StartPollSeconds": ep.get("startPollSeconds", ""),
                "EndPollSeconds": ep.get("endPollSeconds", ""),
                "UncertaintyMinusSeconds": ep.get("uncertaintyMinusSeconds", ""),
                "UncertaintyPlusSeconds": ep.get("uncertaintyPlusSeconds", ""),
                "ContinuityUncertain": bool(ep.get("continuityUncertain")),
                "SessionBreaks": len(ep.get("sessionBreaks", [])),
                "Transitions": len(ep.get("dcTransitions", [])),
            })
        return buf.getvalue()

    def _persist_all(self) -> None:
        atomic_write_json(self.settings_path, self.settings)
        atomic_write_json(self.targets_path, self.targets)
        atomic_write_json(self.catalog_path, self.catalog)
        atomic_write_json(self.price_cache_path, self.price_cache)
        atomic_write_json(self.config_price_cache_path, self.config_price_cache)
        atomic_write_json(self.ovh_pricing_cache_path, self.ovh_pricing_cache)
        atomic_write_json(self.catalog_option_cache_path, self.catalog_option_cache)
        atomic_write_json(self.budget_ledger_path, self.budget_ledger)
        atomic_write_json(self.notification_history_path, self.notification_history[-200:])

    def log_event(self, event: str, target_id: str = "", dcs: str = "", note: str = "") -> None:
        timestamp = now_iso()
        exists = self.events_path.exists()
        with self.events_path.open("a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["Timestamp", "Event", "TargetId", "Datacenters", "Note"])
            if not exists:
                w.writeheader()
            w.writerow({"Timestamp": timestamp, "Event": event, "TargetId": target_id, "Datacenters": dcs, "Note": note})
        self._append_stock_observation(timestamp, event, target_id, dcs, note)
        self.notify_event(event, target_id=target_id, dcs=dcs, note=note)

    def _notification_text(self, event: str, target_id: str, dcs: str, note: str) -> tuple[str, str, str]:
        with self.state_lock:
            target = dict(self.targets.get(target_id) or {})
        name = target.get("name") or target_id or "Kimsufi Stock Commander"
        dc_text = dcs.upper() if dcs else ""
        mapping = {
            "AVAILABLE": ("库存出现", f"{name} 有货" + (f" · {dc_text}" if dc_text else ""), "info"),
            "DC_CHANGE": ("机房库存变化", f"{name}" + (f" · {dc_text}" if dc_text else ""), "info"),
            "SOLD_OUT": ("库存售罄", f"{name} 已售罄", "info"),
            "ORDER_SUCCESS": ("自动下单成功", f"{name}" + (f" · {dc_text}" if dc_text else ""), "success"),
            "ORDER_MISS": ("库存竞争失败", f"{name} 本轮未抢到，后续可安全重试", "info"),
            "ORDER_REVIEW": ("订单需要人工复核", f"{name} 已冻结自动重试", "error"),
            "BUDGET_BLOCKED": ("预算阻止下单", f"{name} · {note or '预算策略未通过'}", "warning"),
            "PRICE_BLOCKED": ("付款策略阻止下单", f"{name} · {note or '付款组合未通过'}", "warning"),
            "CREDENTIALS_MISSING": ("OVH 凭证缺失", f"{name} · {note}", "warning"),
            "POLL_ERROR": ("库存轮询错误", f"{name} · {note}", "error"),
            "HEARTBEAT": ("监控心跳", note or "Stock Commander 正常运行", "info"),
            "TEST": ("测试通知", note or "Kimsufi Stock Commander 通知链正常", "success"),
        }
        return mapping.get(event, (event, f"{name} {note}".strip(), "info"))

    def notify_event(
        self, event: str, target_id: str = "", dcs: str = "", note: str = "", force: bool = False
    ) -> dict[str, Any] | None:
        with self.state_lock:
            cfg = dict(self.settings.get("notifications", {}))
        if not force:
            if not cfg.get("enabled", True):
                return None
            enabled_events = {str(x) for x in cfg.get("events", [])}
            if event not in enabled_events:
                return None
        title, message, severity = self._notification_text(event, target_id, dcs, note)
        item = {
            "id": uuid.uuid4().hex,
            "time": now_iso(),
            "event": event,
            "severity": severity,
            "title": title,
            "message": message[:3500],
            "targetId": target_id,
            "datacenters": dcs,
        }
        with self.state_lock:
            self.notification_history.append(item)
            self.notification_history = self.notification_history[-200:]
            atomic_write_json(self.notification_history_path, self.notification_history)

        if cfg.get("telegramEnabled"):
            threading.Thread(target=self._send_telegram, args=(item, cfg), daemon=True, name="telegram-notify").start()
        return item

    def _send_telegram(self, item: dict[str, Any], cfg: dict[str, Any]) -> None:
        token_env = str(cfg.get("telegramTokenEnv") or "KSC_TELEGRAM_BOT_TOKEN")
        token = os.environ.get(token_env, "").strip()
        chat_id = str(cfg.get("telegramChatId") or "").strip()
        if not token or not chat_id:
            self.notification_last_error = f"Telegram not ready: token env={token_env!r}, chat id configured={bool(chat_id)}"
            return
        silent = item.get("event") in {str(x) for x in cfg.get("silentEvents", [])}
        text = f"[{item.get('event')}] {item.get('title')}\n{item.get('message')}\n{item.get('time')}"
        payload = urlencode({
            "chat_id": chat_id,
            "text": text[:4096],
            "disable_notification": "true" if silent else "false",
        }).encode("utf-8")
        req = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": f"KimsufiStockCommander/{APP_VERSION}"},
        )
        try:
            with urlopen(req, timeout=12) as resp:
                body = resp.read(20000).decode("utf-8", errors="replace")
                if getattr(resp, "status", 200) >= 300:
                    raise RuntimeError(f"Telegram HTTP {resp.status}: {body[:500]}")
                parsed = json.loads(body or "{}")
                if not parsed.get("ok", False):
                    raise RuntimeError(f"Telegram API error: {body[:500]}")
            self.notification_last_error = None
        except Exception as e:
            safe_error = str(e).replace(token, "<redacted-token>") if token else str(e)
            self.notification_last_error = safe_error
            self.log_error(f"telegram notification: {safe_error}")

    def log_error(self, msg: str) -> None:
        with self.error_path.open("a", encoding="utf-8") as f:
            f.write(f"{now_iso()}\t{msg}\n")

    # ---------- CLI ----------

    def cli_exists(self) -> bool:
        if self.demo:
            return True
        return Path(os.path.expandvars(str(self.settings.get("exe", "")))).is_file()

    def run_cli(self, args: list[str], timeout: int = 90) -> CommandResult:
        if self.demo:
            return self._run_demo_cli(args)
        exe = os.path.expandvars(str(self.settings["exe"]))
        full = [exe, *args]
        with self.cli_semaphore:
            proc = subprocess.run(
                full,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        return CommandResult(proc.returncode, proc.stdout or "", full)

    def _run_demo_cli(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult(1, "missing command", args)
        if args[0] == "list":
            return CommandResult(0, DEMO_LIST_OUTPUT, args)
        if args[0] == "check":
            try:
                p = args[args.index("--plan-code") + 1]
            except Exception:
                return CommandResult(1, "missing plan", args)
            if "--list-options" in args:
                variants = parse_check_output(DEMO_CHECKS.get(p, ""))
                memories = list(dict.fromkeys(v.get("memory", "") for v in variants if v.get("memory")))
                storages = list(dict.fromkeys(v.get("storage", "") for v in variants if v.get("storage")))
                lines = ["option    description    default", "-----------    -----------    -------"]
                for i, value in enumerate(memories):
                    lines.append(f"memory={value}    Demo memory {value}    {'true' if i == 0 else 'false'}")
                for i, value in enumerate(storages):
                    lines.append(f"storage={value}    Demo storage {value}    {'true' if i == 0 else 'false'}")
                return CommandResult(0, "\n".join(lines) + "\n", args)
            output = DEMO_CHECKS.get(p, f"{p} is not available in any datacenter\n")
            rows = parse_check_output(output)
            has_available = any(x.get("status") == "available" for x in rows)
            return CommandResult(0 if has_available else 1, output, args)
        if args[0] == "order" and "--list-prices" in args:
            try:
                p = args[args.index("--plan-code") + 1]
            except Exception:
                return CommandResult(1, "missing plan", args)
            return CommandResult(0, DEMO_PRICES.get(p, DEMO_PRICES.get("26skle05", "")), args)
        if args[0] == "order" and "--list-options" in args:
            try:
                p = args[args.index("--plan-code") + 1]
            except Exception:
                return CommandResult(1, "missing plan", args)
            duration = "P1M"
            if "--price-duration" in args:
                duration = args[args.index("--price-duration") + 1]
            multiplier = {"P1M": 1.0, "P3M": 3.0, "P6M": 6.0, "P1Y": 12.0, "P2Y": 24.0}.get(duration, 1.0)
            variants = parse_check_output(DEMO_CHECKS.get(p, ""))
            seen: set[tuple[str,str]] = set()
            lines = ["item-option    name    price", "-----------    ----    -----"]
            for v in variants:
                for family, base in (("memory", v.get("memory", "")), ("storage", v.get("storage", ""))):
                    if not base or (family, base) in seen:
                        continue
                    seen.add((family, base))
                    # Keep memory included; demo storage has a small price so annual budget can verify option aggregation.
                    monthly = 0.0 if family == "memory" else (2.0 if "nvme" in base else 1.0)
                    amount = monthly * multiplier
                    lines.append(f"{family}={base}-{p}    Demo {family} {base}    {amount:.2f} EUR")
            return CommandResult(0, "\n".join(lines) + "\n", args)
        if args[0] == "order":
            # Demo mode never simulates a successful paid order.
            return CommandResult(2, "> error: demo mode blocks checkout", args)
        return CommandResult(1, "unsupported demo command", args)

    # ---------- catalog ----------

    def list_command(self) -> list[str]:
        return [
            "list",
            "--category", str(self.settings["category"]),
            "--endpoint", str(self.settings["endpoint"]),
            "--country", str(self.settings["country"]),
        ]

    def check_command(self, plan_code: str, memory: str | None = None, storage: str | None = None) -> list[str]:
        args = [
            "check",
            "--plan-code", plan_code,
            "--endpoint", str(self.settings["endpoint"]),
            "--country", str(self.settings["country"]),
        ]
        opts = []
        if memory:
            opts.append(f"memory={memory}")
        if storage:
            opts.append(f"storage={storage}")
        if opts:
            args += ["--option", ",".join(opts)]
        return args

    def check_list_options_command(self, plan_code: str) -> list[str]:
        return [
            "check", "--plan-code", plan_code, "--list-options",
            "--endpoint", str(self.settings["endpoint"]),
            "--country", str(self.settings["country"]),
        ]

    def refresh_catalog(self) -> None:
        with self.state_lock:
            if self.catalog_refreshing:
                return
            self.catalog_refreshing = True
            self.last_catalog_error = None
        try:
            if not self.cli_exists():
                raise FileNotFoundError(f"kimsufi-notifier not found: {self.settings.get('exe')}")
            base_result = self.run_cli(self.list_command())
            if base_result.code != 0:
                raise RuntimeError(f"list failed ({base_result.code}): {base_result.output[-1000:]}")
            plans = parse_list_output(base_result.output)

            workers = max(1, min(8, int(self.settings.get("catalogWorkers", 4))))
            variants_by_plan: dict[str, list[dict[str, Any]]] = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(self.run_cli, self.check_command(p["planCode"])): p["planCode"] for p in plans}
                for fut in as_completed(futures):
                    plan_code = futures[fut]
                    try:
                        r = fut.result()
                        # IMPORTANT: kimsufi-notifier intentionally exits 1 when every returned
                        # configuration is unavailable, after printing the full table. Parse first.
                        parsed = parse_check_output(r.output)
                        variants_by_plan[plan_code] = parsed
                        if r.code not in (0, 1):
                            self.log_error(f"catalog check {plan_code}: code={r.code} {r.output[-1000:]}")
                    except Exception as e:
                        variants_by_plan[plan_code] = []
                        self.log_error(f"catalog check {plan_code}: {e}")

            # If the availability endpoint has no rows at all, discover mandatory
            # memory/storage choices from the catalog instead. This keeps sold-out
            # products fully configurable in the UI.
            missing_plans = [p["planCode"] for p in plans if not variants_by_plan.get(p["planCode"])]
            if missing_plans:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(self.run_cli, self.check_list_options_command(plan)): plan for plan in missing_plans}
                    for fut in as_completed(futures):
                        plan_code = futures[fut]
                        try:
                            r = fut.result()
                            opts = parse_catalog_options_output(r.output) if r.code == 0 else {}
                            variants_by_plan[plan_code] = catalog_option_variants(plan_code, opts)
                        except Exception as e:
                            self.log_error(f"catalog option discovery {plan_code}: {e}")

            rows: list[dict[str, Any]] = []
            for p in plans:
                variants = variants_by_plan.get(p["planCode"]) or [{
                    "planCode": p["planCode"],
                    "memory": "",
                    "storage": "",
                    "status": p.get("status", "unknown"),
                    "datacenters": p.get("datacenters", []),
                }]
                for v in variants:
                    row = {
                        **p,
                        **v,
                        "id": self._target_key(p["planCode"], v.get("memory", ""), v.get("storage", "")),
                        "ramGB": parse_ram_gb(v.get("memory", "")),
                        "diskTypes": disk_types(v.get("storage", "")),
                        "diskLabel": humanize_storage(v.get("storage", "")),
                        "memoryLabel": humanize_memory(v.get("memory", "")),
                    }
                    rows.append(row)

            with self.state_lock:
                self.catalog = rows
                self.catalog_updated_at = now_iso()
                atomic_write_json(self.catalog_path, self.catalog)
            self.log_event("CATALOG_REFRESH", note=f"{len(plans)} plans / {len(rows)} variants")
        except Exception as e:
            with self.state_lock:
                self.last_catalog_error = str(e)
            self.log_error(f"catalog refresh: {traceback.format_exc()}")
        finally:
            with self.state_lock:
                self.catalog_refreshing = False

    # ---------- targets ----------

    def upsert_target(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan = str(payload.get("planCode", "")).strip()
        memory = str(payload.get("memory", "")).strip()
        storage = str(payload.get("storage", "")).strip()
        if not plan:
            raise ValueError("planCode is required")
        tid = self._target_key(plan, memory, storage)

        if bool(payload.get("autoOrder")):
            row = next((x for x in self.catalog if str(x.get("id", "")) == tid), None)
            if row is not None:
                ord_state = self.catalog_row_orderability(row)
                if ord_state.get("status") == "availability_only":
                    raise ValueError(
                        "该配置仅存在于 availability 数据中，当前 OVH cart 不提供对应下单 option，"
                        "因此不能启用自动下单。"
                    )

        with self.state_lock:
            t = self.targets.get(tid, {
                "id": tid,
                "planCode": plan,
                "name": str(payload.get("name", plan)),
                "memory": memory,
                "storage": storage,
                "price": float(payload.get("price") or 0),
                "watch": False,
                "autoOrder": False,
                "desiredQty": 1,
                "completedQty": 0,
                "orderPriority": 50,
                "budgetReserveOverride": 0.0,
                "review": False,
                "reviewReason": "",
                "lastState": "unknown",
                "lastDatacenters": [],
                "eligibleDatacenter": "",
                "lastCheckAt": None,
                "orderMemoryOption": "",
                "orderStorageOption": "",
                "orderMemoryIncluded": False,
                "orderStorageIncluded": False,
                "inheritPrice": True,
                "priceDuration": self.settings["orderDefaults"]["priceDuration"],
                "priceMode": self.settings["orderDefaults"]["priceMode"],
                "autoPay": bool(self.settings["orderDefaults"]["autoPay"]),
            })
            for k in ["name", "price", "watch", "autoOrder", "desiredQty", "orderPriority",
                      "budgetReserveOverride", "orderMemoryOption", "orderStorageOption",
                      "inheritPrice", "priceDuration", "priceMode", "autoPay"]:
                if k in payload:
                    t[k] = payload[k]
            t["desiredQty"] = max(1, min(50, int(t.get("desiredQty", 1))))
            t["orderPriority"] = max(0, min(100, int(t.get("orderPriority", 50))))
            t["budgetReserveOverride"] = max(0.0, float(t.get("budgetReserveOverride", 0) or 0))
            # Receipts remain the durable purchase-count source of truth even if a
            # target was removed and re-added in the same live process.
            t["completedQty"] = max(int(t.get("completedQty", 0) or 0), len(self._receipt_glob(tid)))
            if int(t.get("completedQty", 0)) >= int(t.get("desiredQty", 1)):
                t["autoOrder"] = False
                t["lastOrderResult"] = "quota_reached"
            if t.get("autoOrder"):
                t["watch"] = True
            if not t.get("watch"):
                t["autoOrder"] = False
            self.targets[tid] = t
            atomic_write_json(self.targets_path, self.targets)

        if t.get("autoOrder") and (not self._target_option_satisfied(t, "memory") or not self._target_option_satisfied(t, "storage")):
            self.resolve_order_options(tid)
        self.force_poll_event.set()
        self.invalidate_snapshot()
        return dict(self.targets[tid])

    def remove_target(self, target_id: str) -> None:
        with self.state_lock:
            if target_id in self.targets:
                if self.targets[target_id].get("review"):
                    raise ValueError("Clear REVIEW before removing this target.")
                self.targets.pop(target_id)
                atomic_write_json(self.targets_path, self.targets)
        self.invalidate_snapshot()

    def resolve_order_options(self, target_id: str) -> dict[str, Any]:
        with self.state_lock:
            t = self.targets.get(target_id)
            if not t:
                raise KeyError(target_id)
            plan = t["planCode"]
            memory = t.get("memory", "")
            storage = t.get("storage", "")

        # Prefer OVH raw cart option objects because they preserve family,
        # productName and the real option planCode. This uses the exact same
        # matcher as configuration pricing, so UI and checkout cannot disagree.
        public_error = ""
        try:
            pricing = self.public_pricing(plan, force=True)
            option_rows = list(pricing.get("options", []) or [])
            mem_match, mem_method, mem_score, mem_reasons = find_order_option_match(
                option_rows, "memory", memory, str(t.get("orderMemoryOption", "") or ""), plan
            ) if memory else (None, "not_needed", 0.0, [])
            sto_match, sto_method, sto_score, sto_reasons = find_order_option_match(
                option_rows, "storage", storage, str(t.get("orderStorageOption", "") or ""), plan
            ) if storage else (None, "not_needed", 0.0, [])

            mem_default = self.catalog_default_match(plan, "memory", memory, force=False) if memory and mem_match is None else None
            sto_default = self.catalog_default_match(plan, "storage", storage, force=False) if storage and sto_match is None else None
            cache_key = self._catalog_option_cache_key(plan)
            if cache_key not in self.catalog_option_cache:
                if memory and mem_match is None:
                    mem_default = self.catalog_default_match(plan, "memory", memory, force=True)
                if storage and sto_match is None:
                    sto_default = self.catalog_default_match(plan, "storage", storage, force=True)
            with self.state_lock:
                if memory and mem_match is not None:
                    t["orderMemoryOption"] = str(mem_match.get("planCode", "") or "")
                    t["orderMemoryIncluded"] = False
                elif memory and mem_default is not None:
                    t["orderMemoryOption"] = ""
                    t["orderMemoryIncluded"] = True
                if storage and sto_match is not None:
                    t["orderStorageOption"] = str(sto_match.get("planCode", "") or "")
                    t["orderStorageIncluded"] = False
                elif storage and sto_default is not None:
                    t["orderStorageOption"] = ""
                    t["orderStorageIncluded"] = True
                t["optionMatchDiagnostics"] = {
                    "memory": {"method": (mem_method if mem_match is not None else (mem_default or {}).get("method", mem_method)), "score": mem_score, "reasons": mem_reasons + (["included_in_base_plan"] if mem_default else []), "generic": memory},
                    "storage": {"method": (sto_method if sto_match is not None else (sto_default or {}).get("method", sto_method)), "score": sto_score, "reasons": sto_reasons + (["included_in_base_plan"] if sto_default else []), "generic": storage},
                }
                unresolved = []
                if memory and not self._target_option_satisfied(t, "memory"):
                    unresolved.append("memory")
                if storage and not self._target_option_satisfied(t, "storage"):
                    unresolved.append("storage")
                if not unresolved:
                    t["optionResolutionError"] = ""
                    atomic_write_json(self.targets_path, self.targets)
                    return dict(t)
        except Exception as e:
            public_error = str(e)

        # Compatibility fallback to kimsufi-notifier's textual --list-options.
        args = [
            "order", "--plan-code", plan, "--list-options",
            "--endpoint", str(self.settings["endpoint"]),
            "--country", str(self.settings["country"]),
        ]
        r = self.run_cli(args)
        if r.code == 0:            mem = resolve_full_option(r.output, "memory", memory, plan) if memory else ""
            sto = resolve_full_option(r.output, "storage", storage, plan) if storage else ""
            with self.state_lock:
                if memory and mem:
                    t["orderMemoryOption"] = mem
                if storage and sto:
                    t["orderStorageOption"] = sto

        with self.state_lock:
            unresolved = []
            if memory and not self._target_option_satisfied(t, "memory"):
                unresolved.append("memory")
            if storage and not self._target_option_satisfied(t, "storage"):
                unresolved.append("storage")
            parts = []
            if unresolved:
                parts.append("Could not resolve: " + ", ".join(unresolved))
            if public_error:
                parts.append("OVH raw option lookup: " + public_error[:500])
            if r.code != 0:
                parts.append(f"--list-options failed: {r.output[-500:]}")
            t["optionResolutionError"] = " | ".join(parts)
            atomic_write_json(self.targets_path, self.targets)
            return dict(t)

    def _price_cache_key(self, plan_code: str) -> str:
        return f"{self.settings.get('endpoint','')}|{self.settings.get('country','')}|{plan_code}"

    def _ovh_pricing_cache_key(self, plan_code: str) -> str:
        return f"{self.settings.get('endpoint','')}|{self.settings.get('country','')}|{plan_code}"

    def _catalog_option_cache_key(self, plan_code: str) -> str:
        return f"{self.settings.get('endpoint','')}|{self.settings.get('country','')}|{plan_code}"

    def refresh_catalog_option_metadata(self, plan_codes: list[str], *, force: bool = True) -> dict[str, str]:
        plans = sorted(set(str(x).strip() for x in plan_codes if str(x).strip()))
        if not force:
            plans = [x for x in plans if self._catalog_option_cache_key(x) not in self.catalog_option_cache]
        if not plans:
            return {}
        errors: dict[str, str] = {}
        workers = max(1, min(6, int(self.settings.get("catalogWorkers", 4))))
        fetched: dict[str, dict[str, Any]] = {}
        def fetch(plan: str):
            r = self.run_cli(self.check_list_options_command(plan))
            fam = parse_catalog_options_output(r.output)
            if r.code != 0 and not fam:
                raise RuntimeError(f"check --list-options failed ({r.code}): {r.output[-800:]}")
            return plan, {"schemaVersion": CATALOG_OPTION_CACHE_SCHEMA, "planCode": plan, "updatedAt": now_iso(), "families": fam}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs={pool.submit(fetch,p):p for p in plans}
            for fut in as_completed(futs):
                plan=futs[fut]
                try:
                    pp,data=fut.result(); fetched[pp]=data
                except Exception as e:
                    errors[plan]=str(e)
        with self.state_lock:
            for plan,data in fetched.items():
                self.catalog_option_cache[self._catalog_option_cache_key(plan)] = data
            atomic_write_json(self.catalog_option_cache_path, self.catalog_option_cache)
        return errors

    def catalog_option_metadata(self, plan_code: str, *, force: bool = False) -> dict[str, Any]:
        key=self._catalog_option_cache_key(plan_code)
        if force:
            errs=self.refresh_catalog_option_metadata([plan_code], force=True)
            if plan_code in errs:
                raise LookupError(errs[plan_code])
        data=self.catalog_option_cache.get(key)
        if not isinstance(data,dict):
            raise LookupError("catalog option metadata is not cached")
        return data

    def catalog_default_match(self, plan_code: str, family: str, generic: str, *, force: bool = False) -> dict[str, Any] | None:
        try:
            meta=self.catalog_option_metadata(plan_code, force=force)
        except Exception:
            return None
        families=meta.get("families",{}) if isinstance(meta,dict) else {}
        rows=list(families.get(family,[]) or [])
        generic_n=_normalize_option_text(generic)
        # Exact generic token is authoritative. Description semantic fallback is
        # allowed only when there is one unique default candidate.
        for x in rows:
            if _normalize_option_text(x.get("value","")) == generic_n and bool(x.get("default")):
                return {**x,"family":family,"method":"catalog_default_exact"}
        defaults=[x for x in rows if bool(x.get("default"))]
        scored=[]
        for x in defaults:
            pseudo={"family":family,"planCode":str(x.get("value","")),"name":str(x.get("description",""))}
            score,reasons=_semantic_option_score(family,generic,pseudo,plan_code)
            if score>0: scored.append((score,x,reasons))
        scored.sort(key=lambda z:-z[0])
        threshold=90 if family=='memory' else 120 if family=='storage' else 70
        if scored and scored[0][0]>=threshold and (len(scored)==1 or scored[0][0]-scored[1][0]>=8):
            return {**scored[0][1],"family":family,"method":"catalog_default_semantic","score":scored[0][0],"reasons":scored[0][2]}
        return None

    def _target_option_satisfied(self, target: dict[str, Any], family: str) -> bool:
        if family == "memory":
            return (not target.get("memory")) or bool(target.get("orderMemoryOption")) or bool(target.get("orderMemoryIncluded"))
        if family == "storage":
            return (not target.get("storage")) or bool(target.get("orderStorageOption")) or bool(target.get("orderStorageIncluded"))
        return True

    def _ovh_api_base(self) -> str:
        endpoint = str(self.settings.get("endpoint", "ovh-eu"))
        base = OVH_API_BASES.get(endpoint)
        if not base:
            raise ValueError(f"unsupported OVH API endpoint for public pricing: {endpoint}")
        return base.rstrip("/")

    def _ovh_public_json(
        self, method: str, path: str, *, query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None, timeout: int = 45
    ) -> Any:
        url = self._ovh_api_base() + path
        if query:
            url += "?" + urlencode({k: v for k, v in query.items() if v is not None})
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(url, data=data, method=method.upper())
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", f"KimsufiStockCommander/{APP_VERSION}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except Exception as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:1200]  # type: ignore[attr-defined]
            except Exception:
                pass
            raise RuntimeError(f"OVH public API {method.upper()} {path} failed: {e}{(' | '+detail) if detail else ''}") from e
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"OVH public API returned invalid JSON for {path}: {raw[:400]!r}") from e

    def _create_public_pricing_cart(self) -> str:
        expire = datetime.now(timezone.utc).timestamp() + 86400
        expire_iso = datetime.fromtimestamp(expire, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        payload = {
            "description": f"Kimsufi Stock Commander {APP_VERSION} price scan",
            "expire": expire_iso,
            "ovhSubsidiary": str(self.settings.get("country", "IE")),
        }
        obj = self._ovh_public_json("POST", "/order/cart", body=payload)
        cart_id = str((obj or {}).get("cartId", ""))
        if not cart_id:
            raise RuntimeError("OVH public API cart creation returned no cartId")
        return cart_id

    def _delete_public_pricing_cart(self, cart_id: str) -> None:
        try:
            self._ovh_public_json("DELETE", f"/order/cart/{cart_id}", timeout=20)
        except Exception:
            # Price scans must not fail merely because cleanup failed; carts expire automatically.
            pass

    def _normalize_public_base_prices(self, raw: Any, plan_code: str) -> list[dict[str, Any]]:
        infos = raw if isinstance(raw, list) else []
        info = next((x for x in infos if str(x.get("planCode", "")) == plan_code), None)
        if info is None and infos:
            info = infos[0]
        if not isinstance(info, dict):
            return []
        rows: list[dict[str, Any]] = []
        for p in info.get("prices", []) or []:
            if not isinstance(p, dict):
                continue
            price = p.get("price") if isinstance(p.get("price"), dict) else {}
            amount = money_value(price)
            currency = str(price.get("currencyCode", "") or "").upper()
            duration = str(p.get("duration", "") or "")
            mode = str(p.get("pricingMode", "") or "")
            if not duration or not mode or amount is None:
                continue
            rows.append({
                "duration": duration,
                "mode": mode,
                "price": str(price.get("text", "") or f"{amount:.2f} {currency}"),
                "amount": amount,
                "currency": currency,
                "description": str(p.get("description", "") or ""),
                "pricingType": str(p.get("pricingType", "") or ""),
                "interval": p.get("interval"),
                "capacities": list(p.get("capacities", []) or []),
            })
        return rows

    def _normalize_public_options(self, raw: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for opt in raw if isinstance(raw, list) else []:
            if not isinstance(opt, dict):
                continue
            prices: list[dict[str, Any]] = []
            for p in opt.get("prices", []) or []:
                if not isinstance(p, dict):
                    continue
                price = p.get("price") if isinstance(p.get("price"), dict) else {}
                amount = money_value(price)
                if amount is None:
                    continue
                prices.append({
                    "duration": str(p.get("duration", "") or ""),
                    "mode": str(p.get("pricingMode", "") or ""),
                    "amount": amount,
                    "currency": str(price.get("currencyCode", "") or "").upper(),
                    "text": str(price.get("text", "") or ""),
                    "priceInUcents": p.get("priceInUcents"),
                })
            rows.append({
                "family": str(opt.get("family", "") or ""),
                "planCode": str(opt.get("planCode", "") or ""),
                "name": str(opt.get("productName", "") or ""),
                "mandatory": bool(opt.get("mandatory")),
                "prices": prices,
            })
        return rows

    def _demo_public_pricing(self, plan_code: str) -> dict[str, Any]:
        base_prices = parse_price_output(DEMO_PRICES.get(plan_code, DEMO_PRICES.get("26skle05", "")))
        variants = parse_check_output(DEMO_CHECKS.get(plan_code, ""))
        options: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for v in variants:
            for family, generic in (("memory", v.get("memory", "")), ("storage", v.get("storage", ""))):
                if not generic or (family, generic) in seen:
                    continue
                seen.add((family, generic))
                # Deliberately make demo configurations differ so regressions are visible.
                if family == "memory":
                    monthly = 0.0
                else:
                    monthly = 2.0 if "nvme" in generic.lower() else 1.0
                prices = []
                for b in base_prices:
                    months = price_duration_months(str(b.get("duration", ""))) or 1.0
                    prices.append({
                        "duration": b.get("duration", ""), "mode": b.get("mode", ""),
                        "amount": round(monthly * months, 2), "currency": b.get("currency", "EUR"),
                        "text": f"{monthly * months:.2f} {b.get('currency','EUR')}",
                    })
                options.append({
                    "family": family, "planCode": f"{generic}-{plan_code}",
                    "name": f"Demo {family} {generic}", "mandatory": True, "prices": prices,
                })
        return {"planCode": plan_code, "updatedAt": now_iso(), "prices": base_prices, "options": options, "source": "demo"}

    def refresh_public_pricing(self, plan_codes: list[str], *, force: bool = True) -> dict[str, str]:
        plans = sorted(set(str(x).strip() for x in plan_codes if str(x).strip()))
        if not plans:
            return {}
        errors: dict[str, str] = {}
        if self.demo:
            with self.state_lock:
                for plan in plans:
                    data = self._demo_public_pricing(plan)
                    self.ovh_pricing_cache[self._ovh_pricing_cache_key(plan)] = data
                    self.price_cache[self._price_cache_key(plan)] = {
                        "planCode": plan, "updatedAt": data["updatedAt"], "prices": data["prices"], "source": "ovh-public-demo"
                    }
                atomic_write_json(self.ovh_pricing_cache_path, self.ovh_pricing_cache)
                atomic_write_json(self.price_cache_path, self.price_cache)
            return errors

        if not force:
            plans = [p for p in plans if self._ovh_pricing_cache_key(p) not in self.ovh_pricing_cache]
            if not plans:
                return errors

        cart_id = self._create_public_pricing_cart()
        try:
            workers = max(1, min(4, int(self.settings.get("catalogWorkers", 4))))

            def fetch_plan(plan: str) -> tuple[str, dict[str, Any]]:
                base_raw = self._ovh_public_json("GET", f"/order/cart/{cart_id}/eco", query={"planCode": plan})
                options_raw = self._ovh_public_json("GET", f"/order/cart/{cart_id}/eco/options", query={"planCode": plan})
                base_prices = self._normalize_public_base_prices(base_raw, plan)
                options = self._normalize_public_options(options_raw)
                if not base_prices:
                    raise ValueError("OVH /eco returned no usable plan prices")
                return plan, {
                    "planCode": plan, "updatedAt": now_iso(), "prices": base_prices,
                    "options": options, "source": "ovh-public-cart-api",
                }

            fetched: dict[str, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(fetch_plan, p): p for p in plans}
                for fut in as_completed(futs):
                    plan = futs[fut]
                    try:
                        got_plan, data = fut.result()
                        fetched[got_plan] = data
                    except Exception as e:
                        errors[plan] = str(e)
            with self.state_lock:
                for plan, data in fetched.items():
                    self.ovh_pricing_cache[self._ovh_pricing_cache_key(plan)] = data
                    self.price_cache[self._price_cache_key(plan)] = {
                        "planCode": plan, "updatedAt": data["updatedAt"],
                        "prices": data["prices"], "source": "ovh-public-cart-api",
                    }
                atomic_write_json(self.ovh_pricing_cache_path, self.ovh_pricing_cache)
                atomic_write_json(self.price_cache_path, self.price_cache)
        finally:
            self._delete_public_pricing_cart(cart_id)
        return errors

    def public_pricing(self, plan_code: str, *, force: bool = False) -> dict[str, Any]:
        plan = str(plan_code or "").strip()
        if not plan:
            raise ValueError("planCode is required")
        key = self._ovh_pricing_cache_key(plan)
        cached = self.ovh_pricing_cache.get(key)
        if force:
            errors = self.refresh_public_pricing([plan], force=True)
            if plan in errors:
                raise ValueError(errors[plan])
            cached = self.ovh_pricing_cache.get(key)
        if not isinstance(cached, dict):
            raise LookupError("OVH public pricing is not cached")
        return cached

    def list_prices(self, plan_code: str, *, force: bool = True) -> list[dict[str, Any]]:
        plan_code = str(plan_code).strip()
        if not plan_code:
            raise ValueError("planCode is required")
        try:
            data = self.public_pricing(plan_code, force=force)
            rows = data.get("prices") if isinstance(data, dict) else None
            if isinstance(rows, list) and rows:
                return rows
        except Exception as public_error:
            # Compatibility fallback for environments where the public cart endpoint is blocked.
            key = self._price_cache_key(plan_code)
            if not force:
                cached = self.price_cache.get(key) or {}
                rows = cached.get("prices") if isinstance(cached, dict) else None
                if isinstance(rows, list) and rows:
                    return rows
            args = [
                "order", "--plan-code", plan_code, "--list-prices",
                "--endpoint", str(self.settings["endpoint"]),
                "--country", str(self.settings["country"]),
            ]
            r = self.run_cli(args)
            if r.code != 0:
                raise ValueError(f"public pricing failed: {public_error}; --list-prices failed ({r.code}): {r.output[-1200:]}")
            rows = parse_price_output(r.output)
            if not rows:
                raise ValueError(f"public pricing failed: {public_error}; --list-prices returned no parseable rows")
            with self.state_lock:
                self.price_cache[key] = {"planCode": plan_code, "updatedAt": now_iso(), "prices": rows, "source": "cli-fallback"}
                atomic_write_json(self.price_cache_path, self.price_cache)
            return rows

    def payment_profile_snapshot(self, catalog_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows_source = self.catalog if catalog_rows is None else catalog_rows
        plan_codes = sorted(set(str(x.get("planCode", "")) for x in rows_source if x.get("planCode")))
        union: dict[tuple[str, str], dict[str, Any]] = {}
        by_plan: dict[str, list[dict[str, Any]]] = {}
        for plan in plan_codes:
            cached = self.price_cache.get(self._price_cache_key(plan)) or {}
            rows = cached.get("prices") if isinstance(cached, dict) else None
            if not isinstance(rows, list):
                continue
            by_plan[plan] = rows
            for row in rows:
                key = (str(row.get("duration", "")), str(row.get("mode", "")))
                if not all(key):
                    continue
                item = union.setdefault(key, {
                    "duration": key[0], "mode": key[1], "supportCount": 0,
                    "samplePrice": row.get("price", ""), "sampleDescription": row.get("description", ""),
                })
                item["supportCount"] += 1
        profiles = sorted(union.values(), key=lambda x: price_profile_sort_key(x.get("duration", ""), x.get("mode", "")))
        return {"profiles": profiles, "byPlan": by_plan, "cachedPlanCount": len(by_plan), "catalogPlanCount": len(plan_codes)}

    def scan_payment_profiles(self, *, force: bool = True) -> dict[str, Any]:
        if not self.catalog:
            if self.catalog_refreshing:
                raise ValueError("服务器目录正在加载，请等目录状态变为‘已缓存’后再扫描付款组合。")
            self.refresh_catalog()
        if not self.catalog:
            raise ValueError("服务器目录尚未加载成功，无法扫描付款组合。")
        plan_codes = sorted(set(str(x.get("planCode", "")) for x in self.catalog if x.get("planCode")))
        errors = self.refresh_public_pricing(plan_codes, force=force)
        catalog_errors = self.refresh_catalog_option_metadata(plan_codes, force=force)
        snap = self.payment_profile_snapshot()
        snap["errors"] = errors
        snap["catalogOptionErrors"] = catalog_errors
        snap["configurationScan"] = {
            "planCount": len(plan_codes), "cachedPlanCount": len(plan_codes) - len(errors),
            "errors": errors,
            "previews": self.configuration_price_snapshot(),
            "monthlyPreviews": self.configuration_monthly_price_snapshot(),
            "source": "ovh-public-cart-api" if not self.demo else "demo",
        }
        return snap

    def _config_price_cache_key(self, plan_code: str, duration: str, mode: str) -> str:
        return f"{self.settings.get('endpoint','')}|{self.settings.get('country','')}|{plan_code}|{duration}|{mode}"

    def list_order_options(self, plan_code: str, duration: str, mode: str, *, force: bool = True) -> list[dict[str, Any]]:
        # Compatibility view backed by the raw OVH option price arrays. Missing
        # prices stay None; they are never silently converted to zero.
        data = self.public_pricing(plan_code, force=force)
        rows: list[dict[str, Any]] = []
        for opt in data.get("options", []) or []:
            if not opt.get("mandatory"):
                continue
            pr = option_price_for_config(opt, duration, mode)
            rows.append({
                "family": opt.get("family", ""), "planCode": opt.get("planCode", ""),
                "name": opt.get("name", ""),
                "price": "" if pr is None else pr.get("text", ""),
                "amount": None if pr is None else pr.get("amount"),
                "currency": "" if pr is None else pr.get("currency", ""),
                "priceKnown": pr is not None,
            })
        return rows

    def _cached_plan_price(self, plan_code: str, duration: str, mode: str) -> dict[str, Any] | None:
        cached = self.price_cache.get(self._price_cache_key(plan_code)) or {}
        rows = cached.get("prices") if isinstance(cached, dict) else None
        if not isinstance(rows, list):
            return None
        return next((x for x in rows if x.get("duration") == duration and x.get("mode") == mode), None)

    def catalog_row_orderability(self, row: dict[str, Any]) -> dict[str, Any]:
        """Classify availability rows against the current order/cart catalog.

        availability-only means the availability API exposes a combination that
        cannot currently be represented by the cart's mandatory memory/storage
        options (and is not a catalog default). Such rows may still be useful
        for observation, but are not safe candidates for automatic ordering.
        """
        plan_code = str(row.get("planCode", "") or "")
        generic = {
            "memory": str(row.get("memory", "") or ""),
            "storage": str(row.get("storage", "") or ""),
        }
        if not plan_code:
            return {"status": "unknown", "orderable": None, "reason": "plan_missing", "families": {}}

        try:
            pricing = self.public_pricing(plan_code, force=False)
        except Exception as e:
            return {
                "status": "unknown", "orderable": None,
                "reason": f"pricing_not_cached:{e}", "families": {}
            }

        options = list(pricing.get("options", []) or [])
        try:
            self.catalog_option_metadata(plan_code, force=False)
            catalog_meta_ready = True
        except Exception:
            catalog_meta_ready = False

        families: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        unresolved_without_catalog: list[str] = []

        for family in ("memory", "storage"):
            value = generic[family]
            if not value:
                families[family] = {"status": "not_applicable", "genericValue": value}
                continue

            match, method, score, reasons = find_order_option_match(
                options, family, value, "", plan_code
            )
            if match is not None:
                families[family] = {
                    "status": "cart_option",
                    "genericValue": value,
                    "planCode": str(match.get("planCode", "") or ""),
                    "name": str(match.get("name", "") or ""),
                    "method": method,
                    "score": score,
                    "reasons": reasons,
                }
                continue

            default_item = self.catalog_default_match(plan_code, family, value) if catalog_meta_ready else None
            if default_item is not None:
                families[family] = {
                    "status": "included_default",
                    "genericValue": value,
                    "planCode": "",
                    "name": str(default_item.get("description", "") or "Included in base plan"),
                    "method": str(default_item.get("method", "catalog_default")),
                    "score": float(default_item.get("score", 1000) or 1000),
                    "reasons": list(default_item.get("reasons", []) or []),
                }
                continue

            if not catalog_meta_ready:
                unresolved_without_catalog.append(family)
                families[family] = {
                    "status": "unknown",
                    "genericValue": value,
                    "method": method,
                    "score": score,
                    "reasons": reasons,
                }
                continue

            missing.append(family)
            families[family] = {
                "status": "availability_only",
                "genericValue": value,
                "method": method,
                "score": score,
                "reasons": reasons,
            }

        if unresolved_without_catalog:
            return {
                "status": "unknown",
                "orderable": None,
                "reason": "catalog_option_metadata_not_cached",
                "missingFamilies": unresolved_without_catalog,
                "families": families,
            }

        if missing:
            return {
                "status": "availability_only",
                "orderable": False,
                "reason": "cart_missing:" + ",".join(missing),
                "missingFamilies": missing,
                "families": families,
            }

        return {
            "status": "orderable",
            "orderable": True,
            "reason": "ok",
            "missingFamilies": [],
            "families": families,
        }

    def orderability_snapshot(self) -> dict[str, Any]:
        return {
            str(row.get("id", "")): self.catalog_row_orderability(row)
            for row in self.catalog
        }

    def estimate_catalog_row_price(
        self, row: dict[str, Any], duration: str, mode: str, *,
        force_options: bool = False, target: dict[str, Any] | None = None,
        orderability_hint: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        plan_code = str(row.get("planCode", "") or "")
        # When caches are available, explicitly distinguish an availability-only
        # ghost combination from an ordinary price lookup/matcher failure.
        if not force_options:
            orderability = orderability_hint if orderability_hint is not None else self.catalog_row_orderability(row)
            if orderability.get("status") == "availability_only":
                return {
                    "complete": False, "reason": "availability_only", "planCode": plan_code,
                    "duration": duration, "mode": mode, "amount": None, "baseAmount": None,
                    "optionAmount": None, "currency": "", "options": [],
                    "orderability": orderability,
                }
        try:
            pricing = self.public_pricing(plan_code, force=force_options)
        except Exception as e:
            return {
                "complete": False, "reason": f"pricing_not_cached:{e}", "planCode": plan_code,
                "duration": duration, "mode": mode, "amount": None, "baseAmount": None,
                "optionAmount": None, "currency": "", "options": [],
            }
        price_row = next((x for x in pricing.get("prices", []) if x.get("duration") == duration and x.get("mode") == mode), None)
        if not price_row:
            return {
                "complete": False, "reason": "plan_price_missing_for_profile", "planCode": plan_code,
                "duration": duration, "mode": mode, "amount": None, "baseAmount": None,
                "optionAmount": None, "currency": "", "options": [],
            }
        base = max(0.0, float(price_row.get("amount", 0) or 0))
        currency = str(price_row.get("currency", "") or "").upper()
        options = list(pricing.get("options", []) or [])

        selected_full = {
            "memory": str((target or {}).get("orderMemoryOption", "") or ""),
            "storage": str((target or {}).get("orderStorageOption", "") or ""),
        }
        generic = {"memory": str(row.get("memory", "") or ""), "storage": str(row.get("storage", "") or "")}
        option_total = 0.0
        breakdown: list[dict[str, Any]] = []
        complete = True
        reasons: list[str] = []
        matched_raw_families: set[str] = set()

        mandatory = [x for x in options if x.get("mandatory")]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for opt in mandatory:
            grouped.setdefault(str(opt.get("family", "")), []).append(opt)

        for family in ("memory", "storage"):
            if not generic[family] and not selected_full[family]:
                continue
            match, match_method, match_score, match_reasons = find_order_option_match(
                options, family, generic[family], selected_full[family], plan_code
            )
            if match is None:
                default_item = self.catalog_default_match(plan_code, family, generic[family])
                if default_item is not None:
                    breakdown.append({
                        "family": family, "planCode": "", "name": str(default_item.get("description", "") or "Included in base plan"),
                        "amount": 0.0, "currency": currency, "priceKnown": True,
                        "matchMethod": str(default_item.get("method", "catalog_default")),
                        "matchScore": float(default_item.get("score", 1000) or 1000),
                        "matchReasons": list(default_item.get("reasons", []) or []) + ["included_in_base_plan"],
                        "genericValue": generic[family], "includedInBase": True,
                    })
                    continue
                complete = False
                reasons.append(f"option_missing:{family}")
                breakdown.append({
                    "family": family, "planCode": "", "name": "", "amount": None,
                    "currency": currency, "priceKnown": False, "matchMethod": match_method,
                    "matchScore": match_score, "matchReasons": match_reasons,
                    "genericValue": generic[family],
                })
                continue
            matched_raw_families.add(str(match.get("family", "") or ""))
            pr = option_price_for_config(match, duration, mode)
            if pr is None:
                complete = False
                reasons.append(f"option_price_missing:{family}:{duration}/{mode}")
                breakdown.append({
                    "family": family, "planCode": match.get("planCode", ""), "name": match.get("name", ""),
                    "amount": None, "currency": currency, "priceKnown": False,
                    "matchMethod": match_method, "matchScore": match_score,
                    "matchReasons": match_reasons, "genericValue": generic[family],
                })
                continue
            row_currency = str(pr.get("currency", "") or "").upper()
            if row_currency and currency and row_currency != currency:
                complete = False
                reasons.append(f"option_currency_mismatch:{family}")
                continue
            amount = max(0.0, float(pr.get("amount", 0) or 0))
            option_total += amount
            breakdown.append({
                "family": family, "planCode": match.get("planCode", ""), "name": match.get("name", ""),
                "amount": amount, "currency": row_currency or currency, "priceKnown": True,
                "matchMethod": match_method, "matchScore": match_score,
                "matchReasons": match_reasons, "genericValue": generic[family],
            })

        # Mirror kimsufi-notifier: for every other mandatory family, choose the
        # cheapest option under the default monthly profile, then price that same
        # option under the requested profile.
        for family, family_rows in grouped.items():
            if family in {"memory", "storage"} or family in matched_raw_families:
                continue
            chosen = cheapest_mandatory_option(family_rows)
            if chosen is None:
                complete = False
                reasons.append(f"mandatory_option_unknown:{family}")
                continue
            pr = option_price_for_config(chosen, duration, mode)
            if pr is None:
                complete = False
                reasons.append(f"option_price_missing:{family}:{duration}/{mode}")
                breakdown.append({
                    "family": family, "planCode": chosen.get("planCode", ""), "name": chosen.get("name", ""),
                    "amount": None, "currency": currency, "priceKnown": False,
                })
                continue
            row_currency = str(pr.get("currency", "") or "").upper()
            if row_currency and currency and row_currency != currency:
                complete = False
                reasons.append(f"option_currency_mismatch:{family}")
                continue
            amount = max(0.0, float(pr.get("amount", 0) or 0))
            option_total += amount
            breakdown.append({
                "family": family, "planCode": chosen.get("planCode", ""), "name": chosen.get("name", ""),
                "amount": amount, "currency": row_currency or currency, "priceKnown": True,
            })

        total = round(base + option_total, 2) if complete else None
        return {
            "complete": complete, "reason": "ok" if complete else ";".join(reasons),
            "planCode": plan_code, "duration": duration, "mode": mode,
            "amount": total, "baseAmount": round(base, 2),
            "optionAmount": round(option_total, 2), "currency": currency,
            "options": breakdown, "source": str(pricing.get("source", "ovh-public-cart-api")),
            "note": "recurring_or_commitment_price_excl_tax_setup_not_included",
        }

    def configuration_price_snapshot(self) -> dict[str, Any]:
        previews: dict[str, dict[str, Any]] = {}
        for row in self.catalog:
            target = self.targets.get(str(row.get("id", "")))
            duration, mode = self.effective_price_config(target or {})
            previews[str(row.get("id", ""))] = self.estimate_catalog_row_price(
                row, duration, mode, force_options=False, target=target
            )
        return previews

    def configuration_monthly_price_snapshot(self) -> dict[str, Any]:
        previews: dict[str, dict[str, Any]] = {}
        for row in self.catalog:
            target = self.targets.get(str(row.get("id", "")))
            previews[str(row.get("id", ""))] = self.estimate_catalog_row_price(
                row, "P1M", "default", force_options=False, target=target
            )
        return previews

    def pricing_diagnostics(self, row_id: str, *, force: bool = False) -> dict[str, Any]:
        row=next((x for x in self.catalog if str(x.get("id",""))==str(row_id)),None)
        if row is None:
            raise KeyError(row_id)
        plan=str(row.get("planCode","") or "")
        if force:
            self.refresh_public_pricing([plan], force=True)
            self.refresh_catalog_option_metadata([plan], force=True)
        pricing=self.public_pricing(plan, force=False)
        try: catalog_meta=self.catalog_option_metadata(plan, force=False)
        except Exception as e: catalog_meta={"error":str(e),"families":{}}
        monthly=self.estimate_catalog_row_price(row,"P1M","default",force_options=False)
        dur=str(self.settings.get("orderDefaults",{}).get("priceDuration","P1M"))
        mode=str(self.settings.get("orderDefaults",{}).get("priceMode","default"))
        period=self.estimate_catalog_row_price(row,dur,mode,force_options=False)
        return {"row":row,"planCode":plan,"catalogOptions":catalog_meta,"rawCartOptions":pricing.get("options",[]),
                "planPrices":pricing.get("prices",[]),"monthlyEstimate":monthly,"periodEstimate":period,
                "orderability":self.catalog_row_orderability(row)}

    def scan_configuration_prices(self, *, force: bool = True) -> dict[str, Any]:
        if not self.catalog:
            if self.catalog_refreshing:
                raise ValueError("服务器目录正在加载，请等目录状态变为‘已缓存’后再扫描配置价格。")
            self.refresh_catalog()
        if not self.catalog:
            raise ValueError("服务器目录尚未加载成功，无法扫描配置价格。")
        plans = sorted(set(str(x.get("planCode", "")) for x in self.catalog if x.get("planCode")))
        errors = self.refresh_public_pricing(plans, force=force)
        catalog_errors = self.refresh_catalog_option_metadata(plans, force=force)
        return {
            "planCount": len(plans), "cachedPlanCount": len(plans) - len(errors),
            "errors": errors, "catalogOptionErrors": catalog_errors, "previews": self.configuration_price_snapshot(),
            "monthlyPreviews": self.configuration_monthly_price_snapshot(),
            "orderability": self.orderability_snapshot(),
            "source": "ovh-public-cart-api" if not self.demo else "demo",
        }

    def refresh_checkout_estimate(self, target_id: str, duration: str, mode: str) -> dict[str, Any]:
        with self.state_lock:
            target = dict(self.targets.get(target_id) or {})
        if not target:
            return {"complete": False, "reason": "target_missing", "amount": None, "currency": ""}
        row = next((x for x in self.catalog if str(x.get("id", "")) == target_id), None)
        if row is None:
            row = {
                "id": target_id, "planCode": target.get("planCode", ""),
                "memory": target.get("memory", ""), "storage": target.get("storage", ""),
            }
        estimate = self.estimate_catalog_row_price(row, duration, mode, force_options=True, target=target)
        with self.state_lock:
            t = self.targets.get(target_id)
            if t:
                t["resolvedOptionPriceAmount"] = None if estimate.get("optionAmount") is None else round(float(estimate.get("optionAmount") or 0), 2)
                t["resolvedCheckoutEstimateAmount"] = None if estimate.get("amount") is None else round(float(estimate.get("amount") or 0), 2)
                t["resolvedCheckoutEstimateCurrency"] = str(estimate.get("currency", "") or "")
                t["resolvedCheckoutEstimateComplete"] = bool(estimate.get("complete"))
                t["resolvedCheckoutEstimateReason"] = str(estimate.get("reason", ""))
                t["resolvedOptionPriceBreakdown"] = list(estimate.get("options", []))
                atomic_write_json(self.targets_path, self.targets)
        return estimate

    def effective_price_config(self, target: dict[str, Any]) -> tuple[str, str]:
        if target.get("inheritPrice", True):
            defaults = self.settings.get("orderDefaults", {})
            return str(defaults.get("priceDuration", "P1M")), str(defaults.get("priceMode", "default"))
        return str(target.get("priceDuration", "")), str(target.get("priceMode", ""))

    def preflight_price_config(self, target_id: str) -> tuple[bool, str, str]:
        with self.state_lock:
            target = dict(self.targets.get(target_id) or {})
        if not target:
            return False, "", "target_missing"
        duration, mode = self.effective_price_config(target)
        if not duration or not mode:
            return False, "", "price_config_empty"
        try:
            prices = self.list_prices(target["planCode"], force=True)
        except Exception as e:
            with self.state_lock:
                t = self.targets.get(target_id)
                if t:
                    t["priceValidation"] = "error"
                    t["priceValidationMessage"] = str(e)
                    t["lastOrderResult"] = "price_preflight_error"
                    atomic_write_json(self.targets_path, self.targets)
            return False, "", f"price_preflight_error:{e}"
        match = next((x for x in prices if x["duration"] == duration and x["mode"] == mode), None)
        should_notify_block = False
        block_note = ""
        with self.state_lock:
            t = self.targets.get(target_id)
            if t:
                old_validation = t.get("priceValidation")
                old_message = t.get("priceValidationMessage")
                t["effectivePriceDuration"] = duration
                t["effectivePriceMode"] = mode
                t["priceValidationAt"] = now_iso()
                if match:
                    t["priceValidation"] = "ok"
                    t["priceValidationMessage"] = f"{match.get('price','')} {match.get('description','')}".strip()
                    t["resolvedPriceText"] = match.get("price", "")
                    t["resolvedPriceAmount"] = float(match.get("amount", 0) or 0)
                    t["resolvedPriceCurrency"] = str(match.get("currency", "") or "").upper()
                    t["resolvedPriceDescription"] = match.get("description", "")
                else:
                    t["priceValidation"] = "blocked"
                    t["priceValidationMessage"] = f"Unsupported price configuration: {duration}/{mode}"
                    t["lastOrderResult"] = "price_config_unsupported"
                    block_note = t["priceValidationMessage"]
                    should_notify_block = old_validation != "blocked" or old_message != block_note
                atomic_write_json(self.targets_path, self.targets)
        if should_notify_block:
            self.log_event("PRICE_BLOCKED", target_id, note=block_note)
        return (True, duration, mode) if match else (False, "", f"unsupported:{duration}/{mode}")

    def preflight_budget(self, target_id: str) -> tuple[bool, dict[str, Any]]:
        with self.state_lock:
            target = dict(self.targets.get(target_id) or {})
            policy = dict(self.settings.get("budgetPolicy", {}))
        if not target:
            return False, {"reason": "target_missing"}

        base_amount = max(0.0, float(target.get("resolvedCheckoutEstimateAmount", target.get("resolvedPriceAmount", 0)) or 0))
        currency = str(target.get("resolvedCheckoutEstimateCurrency", target.get("resolvedPriceCurrency", "")) or "").upper()
        estimate_complete = bool(target.get("resolvedCheckoutEstimateComplete", True))
        override = max(0.0, float(target.get("budgetReserveOverride", 0) or 0))
        reserve = compute_budget_reserve(
            base_amount,
            float(policy.get("bufferPercent", 0) or 0),
            float(policy.get("bufferFixed", 0) or 0),
            override,
        )
        enabled = bool(policy.get("enabled"))
        budget_currency = str(policy.get("currency", "EUR") or "EUR").upper()
        summary = self.budget_summary()
        reason = "disabled"

        if base_amount <= 0 or not currency:
            ok = not enabled
            reason = "price_amount_unknown" if enabled else "disabled_price_unknown"
        elif enabled and not estimate_complete and override <= 0:
            ok = False
            reason = "checkout_estimate_incomplete"
        elif enabled and currency != budget_currency:
            ok = False
            reason = f"currency_mismatch:{currency}/{budget_currency}"
        else:
            cap = max(0.0, float(policy.get("perOrderReserveCap", 0) or 0))
            total = max(0.0, float(policy.get("totalLimit", 0) or 0))
            used = float(summary.get("usedReserved", 0) or 0)
            if enabled and cap > 0 and reserve > cap + 1e-9:
                ok = False
                reason = f"per_order_cap:{reserve:.2f}>{cap:.2f}"
            elif enabled and total > 0 and used + reserve > total + 1e-9:
                ok = False
                reason = f"total_budget:{used:.2f}+{reserve:.2f}>{total:.2f}"
            else:
                ok = True
                reason = "ok" if enabled else "disabled"

        info = {
            "ok": ok,
            "reason": reason,
            "baseAmount": round(base_amount, 2),
            "currency": currency or budget_currency,
            "reservedAmount": round(reserve, 2),
            "budgetEnabled": enabled,
            "usedReserved": summary.get("usedReserved", 0),
            "remaining": summary.get("remaining"),
            "estimateComplete": estimate_complete,
            "planAmount": round(float(target.get("resolvedPriceAmount", 0) or 0), 2),
            "optionAmount": round(float(target.get("resolvedOptionPriceAmount", 0) or 0), 2),
        }
        should_notify_block = False
        with self.state_lock:
            t = self.targets.get(target_id)
            if t:
                old_budget_validation = t.get("budgetValidation")
                old_budget_reason = t.get("budgetValidationReason")
                t["budgetValidation"] = "ok" if ok else "blocked"
                t["budgetValidationReason"] = reason
                t["budgetBaseAmount"] = info["baseAmount"]
                t["budgetReserveAmount"] = info["reservedAmount"]
                t["budgetCurrency"] = info["currency"]
                t["budgetValidationAt"] = now_iso()
                if not ok:
                    t["lastOrderResult"] = "budget_blocked:" + reason
                    should_notify_block = old_budget_validation != "blocked" or old_budget_reason != reason
                atomic_write_json(self.targets_path, self.targets)
        if should_notify_block:
            self.log_event("BUDGET_BLOCKED", target_id, note=reason)
        return ok, info

    def clear_review(self, target_id: str) -> dict[str, Any]:
        with self.state_lock:
            t = self.targets.get(target_id)
            if not t:
                raise KeyError(target_id)
            self._review_lock_path(target_id).unlink(missing_ok=True)
            self._active_order_path(target_id).unlink(missing_ok=True)
            t["review"] = False
            t["reviewReason"] = ""
            atomic_write_json(self.targets_path, self.targets)
            self.invalidate_snapshot()
        return dict(t)

    # ---------- polling / strategy ----------

    def choose_datacenter(self, available: list[str]) -> tuple[str, str]:
        av = [x.lower() for x in available]
        st = self.settings.get("dcStrategy", {})
        mode = st.get("mode", "any")
        selected = [x.lower() for x in st.get("selected", [])]
        priority = [x.lower() for x in st.get("priority", KNOWN_DCS)]
        fallback = st.get("fallback", "wait")
        fixed = str(st.get("fixed", "")).lower()

        def ordered(candidates: list[str]) -> list[str]:
            out = [x for x in priority if x in candidates]
            out.extend(x for x in candidates if x not in out)
            return out

        if not av:
            return "", "sold_out"
        if mode == "any":
            return ordered(av)[0], "any"
        if mode == "fixed":
            return (fixed, "fixed") if fixed in av else ("", f"waiting_{fixed}")
        if mode == "exclude":
            candidates = [x for x in av if x not in selected]
            return (ordered(candidates)[0], "exclude") if candidates else ("", "excluded_only")
        if mode in ("whitelist", "priority"):
            preferred = [x for x in ordered(selected) if x in av]
            if preferred:
                return preferred[0], "preferred"
            if fallback == "any":
                return ordered(av)[0], "fallback"
            return "", "preferred_unavailable"
        return "", "no_strategy_match"

    def poll_target(self, target_id: str) -> None:
        with self.state_lock:
            t = dict(self.targets.get(target_id) or {})
        if not t or not t.get("watch"):
            return
        r = self.run_cli(self.check_command(t["planCode"], t.get("memory"), t.get("storage")))
        parsed = parse_check_output(r.output)
        if not parsed and re.search(r"(?i)\bis not available in\b", r.output or ""):
            parsed = [{
                "planCode": t["planCode"], "memory": t.get("memory", ""), "storage": t.get("storage", ""),
                "status": "unavailable", "datacenters": [],
            }]
        if not parsed:
            should_notify_error = False
            error_text = r.output[-2000:] or f"exit={r.code}; no parseable availability"
            with self.state_lock:
                tt = self.targets.get(target_id)
                if tt:
                    should_notify_error = tt.get("lastState") != "error" or tt.get("lastError") != error_text
                    tt["lastState"] = "error"
                    tt["lastCheckAt"] = now_iso()
                    tt["lastError"] = error_text
                    atomic_write_json(self.targets_path, self.targets)
            self.log_error(f"poll {target_id}: code={r.code} {r.output[-1000:]}")
            if should_notify_error:
                self.log_event("POLL_ERROR", target_id, note=error_text[:600])
            return

        # Exact check normally returns one row. Merge if the CLI returns more.
        available_dcs: list[str] = []
        state = "unavailable"
        for row in parsed:
            if row.get("status") == "available":
                state = "available"
                for dc in row.get("datacenters", []):
                    if dc not in available_dcs:                        available_dcs.append(dc)

        chosen_dc, reason = self.choose_datacenter(available_dcs)
        with self.state_lock:
            tt = self.targets.get(target_id)
            if not tt:
                return
            old_state = tt.get("lastState")
            old_dcs = list(tt.get("lastDatacenters", []))
            tt["lastState"] = state
            tt["lastDatacenters"] = available_dcs
            tt["eligibleDatacenter"] = chosen_dc
            tt["dcDecision"] = reason
            tt["lastCheckAt"] = now_iso()
            tt["lastError"] = ""
            atomic_write_json(self.targets_path, self.targets)

        if old_state != state:
            self.log_event("AVAILABLE" if state == "available" else "SOLD_OUT", target_id, ",".join(available_dcs))
        elif old_dcs != available_dcs:
            self.log_event("DC_CHANGE", target_id, ",".join(available_dcs), note=f"from={','.join(old_dcs)}")

        if state == "available" and chosen_dc:
            self.maybe_order(target_id, chosen_dc)

    def auth_ready(self) -> tuple[bool, list[str]]:
        missing = [x for x in ("OVH_APP_KEY", "OVH_APP_SECRET", "OVH_CONSUMER_KEY") if not os.environ.get(x)]
        return (not missing, missing)

    def _sync_persistent_order_state(self, target_id: str) -> tuple[bool, dict[str, Any]]:
        """Final fail-closed gate immediately before a checkout attempt.

        Receipts are the durable success source of truth. A surviving active marker
        means the previous attempt is ambiguous and must never be retried
        automatically in the same process or after restart.
        """
        review_path = self._review_lock_path(target_id)
        active_path = self._active_order_path(target_id)

        if review_path.exists():
            reason = "Persistent REVIEW lock exists."
            try:
                reason = review_path.read_text(encoding="utf-8", errors="replace")[:4000]
            except Exception:
                pass
            with self.state_lock:
                t = self.targets.get(target_id)
                if t:
                    t["review"] = True
                    t["reviewReason"] = reason
                    t["lastOrderResult"] = "review"
                    atomic_write_json(self.targets_path, self.targets)
            return False, dict(self.targets.get(target_id) or {})

        if active_path.exists():
            reason = (
                "A persistent active-order marker exists before a new checkout. "
                "The previous attempt may already have created/paid an order; "
                "automatic retry is blocked until REVIEW is cleared manually."
            )
            try:
                self._enter_review(target_id, reason)
            except Exception:
                # Even if the REVIEW marker cannot be persisted (e.g. disk full),
                # the active marker itself remains and is a hard stop on every poll.
                self.log_error(f"failed to persist REVIEW for active marker {target_id}: {traceback.format_exc()}")
                with self.state_lock:
                    t = self.targets.get(target_id)
                    if t:
                        t["review"] = True
                        t["reviewReason"] = reason
                        t["lastOrderResult"] = "review"
            return False, dict(self.targets.get(target_id) or {})

        receipts = self._receipt_glob(target_id)
        with self.state_lock:
            t = self.targets.get(target_id)
            if not t:
                return False, {}
            old_completed = int(t.get("completedQty", 0) or 0)
            completed = max(old_completed, len(receipts))
            desired = max(1, int(t.get("desiredQty", 1) or 1))
            changed = completed != old_completed
            t["completedQty"] = completed
            if completed >= desired:
                # Terminal target state: do not leave a checked auto-order box as
                # the only thing preventing another purchase after a later bug.
                if t.get("autoOrder"):
                    t["autoOrder"] = False
                    changed = True
                t["lastOrderResult"] = "quota_reached"
            if changed:
                atomic_write_json(self.targets_path, self.targets)
            armed = bool(self.settings.get("autoOrderArmed"))
            allowed = armed and bool(t.get("autoOrder")) and not bool(t.get("review")) and completed < desired
            return allowed, dict(t)

    def maybe_order(self, target_id: str, datacenter: str) -> None:
        with self.state_lock:
            t = dict(self.targets.get(target_id) or {})
            armed = bool(self.settings.get("autoOrderArmed"))
            price_cap = float(self.settings.get("orderDefaults", {}).get("catalogMonthlyPriceCap", 0) or 0)
        if not armed or not t.get("autoOrder") or t.get("review"):
            return

        # Cheap early checks before taking the checkout mutex.
        if int(t.get("completedQty", 0)) >= int(t.get("desiredQty", 1)):
            return

        row = next((x for x in self.catalog if str(x.get("id", "")) == target_id), None)
        if row is not None:
            ord_state = self.catalog_row_orderability(row)
            if ord_state.get("status") == "availability_only":
                with self.state_lock:
                    tt = self.targets.get(target_id)
                    if tt:
                        tt["lastOrderResult"] = "availability_only"
                        tt["orderabilityValidation"] = "blocked"
                        tt["orderabilityValidationReason"] = str(ord_state.get("reason", "availability_only"))
                        atomic_write_json(self.targets_path, self.targets)
                return

        # Only one checkout decision can run inside this process.  Crucially, the
        # OSError handler below covers ONLY lock acquisition, never checkout or
        # persistence code.
        if not self.order_lock.acquire(blocking=False):
            return
        guard = None
        try:
            try:
                guard = ExclusiveFileLock(self.data / "order-active.guard", nonblocking=True)
                guard.__enter__()
            except OSError:
                return

            # Re-read receipts/markers only after every in-process checkout is
            # serialized. This closes stale completedQty and surviving marker races.
            allowed, t = self._sync_persistent_order_state(target_id)
            if not allowed:
                return

            # Re-read mutable settings after the final gate.
            with self.state_lock:
                price_cap = float(self.settings.get("orderDefaults", {}).get("catalogMonthlyPriceCap", 0) or 0)

            if price_cap > 0:
                row = next((x for x in self.catalog if str(x.get("id", "")) == target_id), None)
                if row is None:
                    row = {"id": target_id, "planCode": t.get("planCode", ""), "memory": t.get("memory", ""), "storage": t.get("storage", "")}
                monthly = self.estimate_catalog_row_price(row, "P1M", "default", force_options=True, target=t)
                if not monthly.get("complete") or monthly.get("amount") is None:
                    note = f"configuration monthly price unknown; cap={price_cap:.2f}; reason={monthly.get('reason','unknown')}"
                    with self.state_lock:
                        tt = self.targets.get(target_id)
                        if tt:
                            tt["lastOrderResult"] = "monthly_price_unknown"
                            tt["monthlyPriceValidation"] = "blocked"
                            tt["monthlyPriceValidationReason"] = note
                            atomic_write_json(self.targets_path, self.targets)
                    self.notify_event("PRICE_BLOCKED", target_id=target_id, note=note)
                    return
                monthly_amount = float(monthly.get("amount") or 0)
                if monthly_amount > price_cap:
                    note = f"configuration monthly price {monthly_amount:.2f} exceeds cap {price_cap:.2f}"
                    with self.state_lock:
                        tt = self.targets.get(target_id)
                        if tt:
                            tt["lastOrderResult"] = "monthly_price_cap"
                            tt["monthlyPriceValidation"] = "blocked"
                            tt["monthlyPriceValidationReason"] = note
                            atomic_write_json(self.targets_path, self.targets)
                    self.notify_event("PRICE_BLOCKED", target_id=target_id, note=note)
                    return

            ok, missing = self.auth_ready()
            if not ok:
                missing_text = ",".join(missing)
                should_notify = False
                with self.state_lock:
                    tt = self.targets.get(target_id)
                    if tt:
                        new_result = "credentials_missing:" + missing_text
                        should_notify = tt.get("lastOrderResult") != new_result
                        tt["lastOrderResult"] = new_result
                        atomic_write_json(self.targets_path, self.targets)
                if should_notify:
                    self.log_event("CREDENTIALS_MISSING", target_id, note=missing_text)
                return

            if not self._target_option_satisfied(t, "memory") or not self._target_option_satisfied(t, "storage"):
                self.resolve_order_options(target_id)
                with self.state_lock:
                    t = dict(self.targets.get(target_id) or {})
                if not self._target_option_satisfied(t, "memory") or not self._target_option_satisfied(t, "storage"):
                    return

            price_ok, price_duration, price_mode = self.preflight_price_config(target_id)
            if not price_ok:
                return

            # Build a billing-period-aware estimate: selected plan price + selected
            # memory/storage option prices. Tax/setup fees remain outside the public
            # pricing model and are covered by the configured budget buffer.
            self.refresh_checkout_estimate(target_id, price_duration, price_mode)
            budget_ok, budget_info = self.preflight_budget(target_id)
            if not budget_ok:
                return

            # For unattended auto-pay, a finite aggregate budget is mandatory by
            # default. This is a last-resort financial circuit breaker independent
            # of desiredQty and receipts.
            with self.state_lock:
                current = dict(self.targets.get(target_id) or {})
                policy = dict(self.settings.get("budgetPolicy", {}) or {})
                auto_pay = bool(current.get("autoPay", self.settings.get("orderDefaults", {}).get("autoPay", True)))
            if auto_pay and bool(policy.get("requireFiniteForAutoPay", True)):
                total_limit = float(policy.get("totalLimit", 0) or 0)
                if not bool(policy.get("enabled")) or total_limit <= 0:
                    note = "unattended auto-pay requires budgetPolicy.enabled=true and a finite totalLimit > 0"
                    with self.state_lock:
                        tt = self.targets.get(target_id)
                        if tt:
                            tt["lastOrderResult"] = "finite_budget_required"
                            tt["budgetValidation"] = "blocked"
                            tt["budgetValidationReason"] = note
                            atomic_write_json(self.targets_path, self.targets)
                    self.notify_event("BUDGET_BLOCKED", target_id=target_id, note=note)
                    return

            try:
                self._perform_single_order(target_id, datacenter, price_duration, price_mode, budget_info)
            except Exception:
                # Any failure after the persistent active marker is created is
                # ambiguous. Do not swallow it as a file-lock miss. Persist REVIEW
                # if possible and leave the active marker in place as a second hard
                # stop if persistence itself is failing.
                reason = f"Unexpected exception during/after checkout:\n{traceback.format_exc()}"
                try:
                    self._enter_review(target_id, reason)
                except Exception:
                    self.log_error(f"failed to persist REVIEW after checkout exception {target_id}: {traceback.format_exc()}")
                    with self.state_lock:
                        tt = self.targets.get(target_id)
                        if tt:
                            tt["review"] = True
                            tt["reviewReason"] = reason
                            tt["lastOrderResult"] = "review"
                raise
        finally:
            if guard is not None:
                try:
                    guard.__exit__(None, None, None)
                except Exception:
                    self.log_error(f"order guard release {target_id}: {traceback.format_exc()}")
            self.order_lock.release()

    def _perform_single_order(
        self, target_id: str, datacenter: str, price_duration: str, price_mode: str,
        budget_info: dict[str, Any]
    ) -> None:
        with self.state_lock:
            t = dict(self.targets[target_id])
        active = self._active_order_path(target_id)
        durable_write_text(
            active,
            f"ACTIVE ORDER\nStarted={now_iso()}\nTarget={target_id}\nDatacenter={datacenter}\n"
            f"Price={price_duration}/{price_mode}\nBudgetReserve={budget_info.get('reservedAmount', 0)} "
            f"{budget_info.get('currency', '')}\n",
        )

        args = [
            "order",
            "--plan-code", t["planCode"],
            "--endpoint", str(self.settings["endpoint"]),
            "--country", str(self.settings["country"]),
            "--datacenter", datacenter,
            "--quantity", "1",
            "--price-duration", price_duration,
            "--price-mode", price_mode,
        ]
        item_opts = []
        if t.get("orderMemoryOption"):
            item_opts.append(f"memory={t['orderMemoryOption']}")
        if t.get("orderStorageOption"):
            item_opts.append(f"storage={t['orderStorageOption']}")
        if item_opts:
            args += ["--item-option", ",".join(item_opts)]
        if bool(t.get("autoPay", self.settings["orderDefaults"]["autoPay"])):
            args.append("--auto-pay")

        stamp = now_iso()
        with self.orders_path.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 70 + "\n")
            f.write(
                f"{stamp}\nTarget: {target_id}\nCommand: {format_command(args)}\n"
                f"BudgetReserve: {budget_info.get('reservedAmount', 0):.2f} {budget_info.get('currency', '')}\n"
            )

        try:
            r = self.run_cli(args, timeout=180)
        except Exception:
            reason = f"Checkout process exception:\n{traceback.format_exc()}"
            self._enter_review(target_id, reason)
            # Keep active marker. clear_review() removes both persistent stops.
            return

        text = r.output or ""
        with self.orders_path.open("a", encoding="utf-8") as f:
            f.write(f"ExitCode: {r.code}\n{text}\n")

        completed_line = next((line for line in text.splitlines() if "order completed:" in line.lower()), None)
        if r.code == 0 and completed_line:
            # CheckoutCart only prints this line after its HTTP call returned
            # without error. Persist financial evidence before clearing the active
            # marker. If any persistence step fails, the marker survives and the
            # outer gate blocks every automatic retry.
            with self.state_lock:
                tt = self.targets[target_id]
                durable_count = len(self._receipt_glob(target_id))
                next_n = max(int(tt.get("completedQty", 0) or 0), durable_count) + 1
            receipt = self.data / "receipts" / f"{self._target_hash(target_id)}-success-{next_n:03d}.txt"
            durable_write_text(
                receipt,
                f"SUCCESS\nTime={stamp}\nTarget={target_id}\nDatacenter={datacenter}\n"
                f"PriceDuration={price_duration}\nPriceMode={price_mode}\n"
                f"BasePrice={float(budget_info.get('baseAmount', 0) or 0):.2f}\n"
                f"Currency={budget_info.get('currency', '')}\n"
                f"BudgetReserve={float(budget_info.get('reservedAmount', 0) or 0):.2f}\n"
                f"ExitCode={r.code}\nConfirmation={completed_line}\n",
            )
            self._append_budget_entry(
                receipt.name, target_id,
                float(budget_info.get("baseAmount", 0) or 0),
                float(budget_info.get("reservedAmount", 0) or 0),
                str(budget_info.get("currency", "")),
                price_duration, price_mode,
            )
            with self.state_lock:
                tt = self.targets[target_id]
                completed = max(int(tt.get("completedQty", 0) or 0) + 1, len(self._receipt_glob(target_id)))
                tt["completedQty"] = completed
                tt["lastOrderResult"] = "success"
                tt["lastOrderAt"] = stamp
                desired = max(1, int(tt.get("desiredQty", 1) or 1))
                if completed >= desired:
                    tt["autoOrder"] = False
                    tt["lastOrderResult"] = "success_quota_reached"
                atomic_write_json(self.targets_path, self.targets)

            # At this point success evidence, budget ledger and target quota are
            # durable. It is now safe to clear the ambiguous-attempt marker.
            active.unlink(missing_ok=True)
            self.log_event("ORDER_SUCCESS", target_id, datacenter, completed_line)
            return

        has_not_available = bool(re.search(r"(?im)datacenter\s+\S+\s+not available", text))
        has_explicit_error = bool(re.search(r"(?im)^\s*>\s*error:", text))
        if r.code == 0 and has_not_available and not has_explicit_error:
            with self.state_lock:
                tt = self.targets[target_id]
                tt["lastOrderResult"] = "stock_miss"
                tt["lastOrderAt"] = stamp
                atomic_write_json(self.targets_path, self.targets)
            # kimsufi-notifier prints this only when CheckoutCart returned the
            # explicit OVH not-available error. No completed order was confirmed.
            active.unlink(missing_ok=True)
            self.log_event("ORDER_MISS", target_id, datacenter, "Clean stock race lost; safe to retry.")
            return

        self._enter_review(
            target_id,
            "Automatic ordering disarmed for this target because no positive "
            "\"order completed:\" confirmation was seen and the result was not "
            f"a clean stock race.\nExitCode={r.code}\n\n{text[-8000:]}",
        )
        # Deliberately keep active marker on ambiguous outcomes. Manual
        # clear_review() removes both REVIEW and active markers together.

    def _enter_review(self, target_id: str, reason: str) -> None:
        lock = self._review_lock_path(target_id)
        durable_write_text(
            lock,
            f"REVIEW REQUIRED\nTime={now_iso()}\nTarget={target_id}\n\n{reason}\n",
        )
        with self.state_lock:
            t = self.targets.get(target_id)
            if t:
                t["review"] = True
                t["reviewReason"] = reason
                t["lastOrderResult"] = "review"
                t["autoOrder"] = False
                atomic_write_json(self.targets_path, self.targets)
            # An ambiguous checkout is an account-level financial uncertainty,
            # not merely a target-level inconvenience. Stop every automatic order
            # until the operator checks OVH and explicitly re-arms the system.
            self.settings["autoOrderArmed"] = False
            atomic_write_json(self.settings_path, self.settings)
        self.log_event("ORDER_REVIEW", target_id, note=reason[:1000])

    # ---------- settings / runtime ----------

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        allowed_top = {
            "exe", "endpoint", "country", "category", "pollIntervalSeconds",
            "catalogRefreshSeconds", "catalogWorkers", "autoOrderArmed",
            "orderDefaults", "budgetPolicy", "notifications", "dcStrategy",
        }
        patch = {k: v for k, v in patch.items() if k in allowed_top}
        with self.state_lock:
            old_endpoint = str(self.settings.get("endpoint", ""))
            old_country = str(self.settings.get("country", ""))
            self.settings = deep_merge(self.settings, patch)
            self.settings["pollIntervalSeconds"] = max(10, min(3600, int(self.settings.get("pollIntervalSeconds", 30))))
            self.settings["catalogRefreshSeconds"] = max(60, min(86400, int(self.settings.get("catalogRefreshSeconds", 900))))
            self.settings["catalogWorkers"] = max(1, min(8, int(self.settings.get("catalogWorkers", 4))))
            st = self.settings["dcStrategy"]
            st["mode"] = st.get("mode") if st.get("mode") in {"any","fixed","whitelist","priority","exclude"} else "any"
            st["fallback"] = st.get("fallback") if st.get("fallback") in {"wait","any"} else "wait"
            st["selected"] = [str(x).lower() for x in st.get("selected", [])]
            st["priority"] = list(dict.fromkeys([str(x).lower() for x in st.get("priority", KNOWN_DCS)] + KNOWN_DCS))
            self.settings["autoOrderArmed"] = bool(self.settings.get("autoOrderArmed"))
            bp = self.settings.get("budgetPolicy", {})
            bp["enabled"] = bool(bp.get("enabled"))
            bp["currency"] = str(bp.get("currency", "EUR") or "EUR").upper()[:8]
            for key in ("totalLimit", "baselineReserved", "perOrderReserveCap", "bufferPercent", "bufferFixed"):
                bp[key] = max(0.0, float(bp.get(key, 0) or 0))
            bp["bufferPercent"] = min(500.0, bp["bufferPercent"])
            bp["requireFiniteForAutoPay"] = bool(bp.get("requireFiniteForAutoPay", True))
            self.settings["budgetPolicy"] = bp
            if self.settings.get("autoOrderArmed") and bp.get("requireFiniteForAutoPay", True):
                default_auto_pay = bool(self.settings.get("orderDefaults", {}).get("autoPay", True))
                has_autopay_target = any(
                    bool(t.get("autoOrder")) and bool(t.get("autoPay", default_auto_pay))
                    for t in self.targets.values()
                )
                if has_autopay_target and (not bp.get("enabled") or float(bp.get("totalLimit", 0) or 0) <= 0):
                    self.settings["autoOrderArmed"] = False
                    atomic_write_json(self.settings_path, self.settings)
                    raise ValueError("AutoPay 无人值守安全模式要求先启用预算闸门并设置有限的自动购买总预算 (> 0)。")
            nt = self.settings.get("notifications", {})
            nt["enabled"] = bool(nt.get("enabled", True))
            nt["desktop"] = bool(nt.get("desktop", True))
            nt["telegramEnabled"] = bool(nt.get("telegramEnabled", False))
            nt["telegramChatId"] = str(nt.get("telegramChatId", ""))[:256]
            nt["telegramTokenEnv"] = re.sub(r"[^A-Za-z0-9_]", "", str(nt.get("telegramTokenEnv", "KSC_TELEGRAM_BOT_TOKEN")))[:128] or "KSC_TELEGRAM_BOT_TOKEN"
            nt["heartbeatHours"] = max(0.0, min(168.0, float(nt.get("heartbeatHours", 6) or 0)))
            allowed_events = {
                "AVAILABLE", "DC_CHANGE", "SOLD_OUT", "ORDER_SUCCESS", "ORDER_MISS", "ORDER_REVIEW",
                "BUDGET_BLOCKED", "PRICE_BLOCKED", "CREDENTIALS_MISSING", "POLL_ERROR", "HEARTBEAT"
            }
            nt["events"] = [str(x) for x in nt.get("events", []) if str(x) in allowed_events]
            nt["silentEvents"] = [str(x) for x in nt.get("silentEvents", []) if str(x) in allowed_events]
            self.settings["notifications"] = nt
            if old_endpoint != str(self.settings.get("endpoint", "")) or old_country != str(self.settings.get("country", "")):
                self.price_cache = {}
                self.config_price_cache = {}
                self.ovh_pricing_cache = {}
                atomic_write_json(self.price_cache_path, self.price_cache)
                atomic_write_json(self.config_price_cache_path, self.config_price_cache)
                atomic_write_json(self.ovh_pricing_cache_path, self.ovh_pricing_cache)
            atomic_write_json(self.settings_path, self.settings)
        self.force_poll_event.set()
        self.invalidate_snapshot()
        return dict(self.settings)

    def invalidate_snapshot(self) -> int:
        """Mark cached /api/state as stale after a mutable-state change."""
        with self.state_lock:
            self.snapshot_generation += 1
            # Preserve the last snapshot for stale-while-revalidate callers, but
            # force the next free builder to rebuild immediately.
            self.snapshot_cache_at = 0.0
            return self.snapshot_generation

    def _build_snapshot(self) -> dict[str, Any]:
        started = time.monotonic()
        ok, missing = self.auth_ready()

        # Copy only the mutable primitives while holding state_lock.  The heavy
        # derived views below intentionally run outside it so settings saves,
        # polling and other API calls cannot be starved by a large catalog.
        with self.state_lock:
            catalog = [dict(x) for x in self.catalog]
            targets = {k: dict(v) for k, v in self.targets.items()}
            settings = json.loads(json.dumps(self.settings))
            notifications = list(reversed([dict(x) for x in self.notification_history[-50:]]))
            runtime_base = {
                "catalogRefreshing": self.catalog_refreshing,
                "catalogUpdatedAt": self.catalog_updated_at,
                "catalogError": self.last_catalog_error,
                "lastPollAt": self.last_poll_at,
                "startedAt": self.started_at,
                "notificationLastError": self.notification_last_error,
            }

        dcs = sorted(set(KNOWN_DCS + [dc for row in catalog for dc in row.get("datacenters", [])]))

        # Compute orderability once per row and reuse it for both price views.
        orderability: dict[str, Any] = {}
        period_previews: dict[str, Any] = {}
        monthly_previews: dict[str, Any] = {}
        for row in catalog:
            rid = str(row.get("id", ""))
            target = targets.get(rid)
            ord_state = self.catalog_row_orderability(row)
            orderability[rid] = ord_state
            duration, mode = self.effective_price_config(target or {})
            period_previews[rid] = self.estimate_catalog_row_price(
                row, duration, mode, force_options=False, target=target,
                orderability_hint=ord_state,
            )
            monthly_previews[rid] = self.estimate_catalog_row_price(
                row, "P1M", "default", force_options=False, target=target,
                orderability_hint=ord_state,
            )

        built_ms = round((time.monotonic() - started) * 1000.0, 1)
        runtime_base.update({
            "cliFound": self.cli_exists(),
            "authReady": ok,
            "missingCredentials": missing,
            "knownDatacenters": dcs,
            "telegramTokenReady": bool(os.environ.get(str(settings.get("notifications", {}).get("telegramTokenEnv", "KSC_TELEGRAM_BOT_TOKEN")))),
            "snapshotBuildMs": built_ms,
            "snapshotBuilding": True,
        })
        return {
            "version": APP_VERSION,
            "stateRevision": int(self.snapshot_generation),
            "demo": self.demo,
            "settings": settings,
            "catalog": catalog,
            "targets": targets,
            "budget": self.budget_summary(),
            "paymentProfiles": self.payment_profile_snapshot(catalog),
            "configurationPricePreviews": period_previews,
            "configurationMonthlyPricePreviews": monthly_previews,
            "orderability": orderability,
            "notifications": notifications,
            "runtime": runtime_base,
        }

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        cached = self.snapshot_cache
        current_generation = int(self.snapshot_generation)
        # Reuse only a cache that belongs to the current logical state revision.
        if (cached is not None and self.snapshot_cache_generation == current_generation
                and now - self.snapshot_cache_at < 0.75):
            return cached

        if not self.snapshot_build_lock.acquire(blocking=False):
            # Never queue duplicate expensive state builds. A stale cache is still
            # preferable to connection pile-up; the Web UI keeps optimistic target
            # edits overlaid until a snapshot at/after the save acknowledges them.
            if cached is not None:
                return cached
            self.snapshot_build_lock.acquire()
            self.snapshot_build_lock.release()
            if self.snapshot_cache is not None:
                return self.snapshot_cache
            self.snapshot_build_lock.acquire()
        try:
            self.snapshot_building = True
            # If state mutates while a snapshot is being built, rebuild once from
            # the newer revision instead of committing the stale result.
            snap = None
            built_generation = -1
            for _attempt in range(2):
                built_generation = int(self.snapshot_generation)
                candidate = self._build_snapshot()
                if int(self.snapshot_generation) == built_generation:
                    snap = candidate
                    break
                snap = candidate
            assert snap is not None
            self.snapshot_last_build_ms = float(snap.get("runtime", {}).get("snapshotBuildMs", 0) or 0)
            snap["stateRevision"] = int(built_generation)
            snap["runtime"]["snapshotBuilding"] = False
            snap["runtime"]["snapshotAgeMs"] = 0
            # Only cache a snapshot that still represents the current state.
            if int(self.snapshot_generation) == built_generation:
                self.snapshot_cache = snap
                self.snapshot_cache_generation = built_generation
                self.snapshot_cache_at = time.monotonic()
            return snap
        finally:
            self.snapshot_building = False
            if self.snapshot_build_lock.locked():
                self.snapshot_build_lock.release()

    def health_status(self) -> dict[str, Any]:
        age_ms = None
        if self.snapshot_cache is not None and self.snapshot_cache_at > 0:
            age_ms = round(max(0.0, (time.monotonic() - self.snapshot_cache_at) * 1000.0), 1)
        return {
            "ok": True,
            "version": APP_VERSION,
            "snapshotBuilding": bool(self.snapshot_building),
            "snapshotAgeMs": age_ms,
            "snapshotLastBuildMs": self.snapshot_last_build_ms,
            "stateRevision": int(self.snapshot_generation),
            "snapshotRevision": int(self.snapshot_cache_generation),
        }

    def start_worker(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.worker_thread = threading.Thread(target=self._worker_loop, name="stock-worker", daemon=True)
        self.worker_thread.start()

    def _worker_loop(self) -> None:
        next_catalog = 0.0
        next_poll = 0.0
        last_heartbeat = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            if self.catalog_refresh_event.is_set() or now >= next_catalog or not self.catalog:
                self.catalog_refresh_event.clear()
                self.refresh_catalog()
                next_catalog = time.monotonic() + int(self.settings.get("catalogRefreshSeconds", 900))

            if self.force_poll_event.is_set() or now >= next_poll:
                self.force_poll_event.clear()
                with self.state_lock:
                    tids = [
                        tid for tid, _ in sorted(
                            ((tid, t) for tid, t in self.targets.items() if t.get("watch")),
                            key=lambda item: (
                                1 if item[1].get("autoOrder") else 0,
                                int(item[1].get("orderPriority", 50)),
                            ),
                            reverse=True,
                        )
                    ]
                for tid in tids:
                    if self.stop_event.is_set():
                        break
                    try:
                        self.poll_target(tid)
                    except Exception:
                        self.log_error(f"poll target {tid}: {traceback.format_exc()}")
                self.last_poll_at = now_iso()
                next_poll = time.monotonic() + int(self.settings.get("pollIntervalSeconds", 30))

            heartbeat_hours = float(self.settings.get("notifications", {}).get("heartbeatHours", 6) or 0)
            if heartbeat_hours > 0 and now - last_heartbeat >= max(60.0, heartbeat_hours * 3600.0):
                watched = sum(1 for t in self.targets.values() if t.get("watch"))
                armed = bool(self.settings.get("autoOrderArmed"))
                self.log_event("HEARTBEAT", note=f"运行正常 · watched={watched} · autoOrderArmed={armed}")
                last_heartbeat = now
            elif heartbeat_hours <= 0:
                last_heartbeat = now

            self.stop_event.wait(0.5)


def _parse_event_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _safe_event_epoch(value: Any) -> float:
    dt = _parse_event_datetime(value)
    if dt is None:
        return 0.0
    try:
        return dt.timestamp()
    except Exception:
        return 0.0


def _normalize_history_dcs(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").split(",")
    return list(dict.fromkeys(str(x).strip().lower() for x in raw if str(x).strip()))


def _poll_seconds(value: Any) -> int | None:
    try:
        n = int(value)
        return n if n > 0 else None
    except Exception:
        return None


def _stock_record_key(rec: dict[str, Any]) -> str:
    dcs = ",".join(_normalize_history_dcs(rec.get("datacenters")))
    raw = "\x1f".join([
        str(rec.get("time", "")).strip(),
        str(rec.get("event", "")).strip().upper(),
        str(rec.get("targetId", "")).strip(),
        dcs,
        str(rec.get("note", "")).strip(),
    ])
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def build_stock_episodes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert stock transition observations into detection windows.

    A repeated AVAILABLE while an episode is already open is treated as a
    resumption after a transient error, not a new restock. Orphan SOLD_OUT rows
    (common when a watcher starts while already unavailable) are ignored.
    """
    ordered = sorted(records, key=lambda x: _safe_event_epoch(x.get("time")))
    open_by_target: dict[str, dict[str, Any]] = {}
    episodes: list[dict[str, Any]] = []

    for rec in ordered:
        event = str(rec.get("event", "")).upper()
        target_id = str(rec.get("targetId", "")).strip()
        if event == "SESSION_START":
            when = str(rec.get("time", ""))
            epoch = _safe_event_epoch(when)
            if epoch > 0:
                for current in open_by_target.values():
                    current["continuityUncertain"] = True
                    current.setdefault("sessionBreaks", []).append({"time": when, "sessionId": str(rec.get("sessionId", ""))})
            continue
        if not target_id or event not in {"AVAILABLE", "SOLD_OUT", "DC_CHANGE"}:
            continue
        when = str(rec.get("time", ""))
        epoch = _safe_event_epoch(when)
        if epoch <= 0:
            continue
        dcs = _normalize_history_dcs(rec.get("datacenters"))
        poll = _poll_seconds(rec.get("pollIntervalSeconds"))
        current = open_by_target.get(target_id)

        if event == "AVAILABLE":
            if current is not None:
                current["availableDetections"] = int(current.get("availableDetections", 1)) + 1
                current["lastObservation"] = when
                current["datacenters"] = list(dict.fromkeys(current.get("datacenters", []) + dcs))
                continue
            ep = {
                "targetId": target_id,
                "start": when,
                "startEpoch": epoch,
                "end": None,
                "endEpoch": None,
                "closed": False,
                "observedSeconds": None,
                "datacenters": list(dcs),
                "startDatacenters": list(dcs),
                "startPollSeconds": poll,
                "endPollSeconds": None,
                "uncertaintyMinusSeconds": None,
                "uncertaintyPlusSeconds": poll,
                "availableDetections": 1,
                "dcTransitions": [],
                "continuityUncertain": False,
                "sessionBreaks": [],
                "lastObservation": when,
            }
            open_by_target[target_id] = ep
            episodes.append(ep)
            continue

        if event == "DC_CHANGE":
            if current is None:
                continue
            current["datacenters"] = list(dict.fromkeys(current.get("datacenters", []) + dcs))
            current["dcTransitions"].append({"time": when, "datacenters": list(dcs), "note": str(rec.get("note", ""))})
            current["lastObservation"] = when
            continue

        if event == "SOLD_OUT":
            if current is None:
                continue
            current["end"] = when
            current["endEpoch"] = epoch
            current["closed"] = True
            current["observedSeconds"] = max(0, int(round(epoch - float(current.get("startEpoch", epoch)))))
            current["endPollSeconds"] = poll
            current["uncertaintyMinusSeconds"] = poll
            current["uncertaintyPlusSeconds"] = current.get("startPollSeconds")
            current["lastObservation"] = when
            open_by_target.pop(target_id, None)

    return episodes


def simulation_budget_reserve(target: dict[str, Any], budget_policy: dict[str, Any], currency: str) -> float | None:
    override = max(0.0, float(target.get("budgetReserveOverride", 0) or 0))
    if override > 0:
        return round(override, 2)
    prior = max(0.0, float(target.get("budgetReserveAmount", 0) or 0))
    prior_currency = str(target.get("budgetCurrency", "") or "").upper()
    if prior > 0 and (not prior_currency or prior_currency == currency):
        return round(prior, 2)
    resolved = max(0.0, float(target.get("resolvedCheckoutEstimateAmount", target.get("resolvedPriceAmount", 0)) or 0))
    resolved_currency = str(target.get("resolvedCheckoutEstimateCurrency", target.get("resolvedPriceCurrency", "")) or "").upper()
    if resolved > 0 and (not resolved_currency or resolved_currency == currency):
        return compute_budget_reserve(
            resolved,
            float(budget_policy.get("bufferPercent", 0) or 0),
            float(budget_policy.get("bufferFixed", 0) or 0),
            0,
        )
    return None


def first_poll_hit(segments: list[tuple[float, float]], interval_seconds: int, phase_seconds: int) -> float | None:
    interval = max(1.0, float(interval_seconds))
    phase = float(phase_seconds) % interval
    best = None
    for a, b in segments:
        a, b = float(a), float(b)
        if b <= a:
            continue
        k = math.ceil((a - phase) / interval)
        t = phase + k * interval
        if t < b - 1e-9:
            best = t if best is None else min(best, t)
    return best


def normalize_replay_dc_strategy(strategy: dict[str, Any], known_dcs: list[str]) -> dict[str, Any]:
    mode = str(strategy.get("mode", "any") or "any").lower()
    if mode not in {"any", "fixed", "whitelist", "priority", "exclude"}:
        mode = "any"
    selected = list(dict.fromkeys(str(x).lower().strip() for x in strategy.get("selected", []) if str(x).strip()))
    priority = list(dict.fromkeys(str(x).lower().strip() for x in strategy.get("priority", []) if str(x).strip()))
    priority += [x for x in known_dcs if x not in priority]
    fallback = str(strategy.get("fallback", "wait") or "wait").lower()
    if fallback not in {"wait", "any"}:
        fallback = "wait"
    return {
        "mode": mode,
        "fixed": str(strategy.get("fixed", "") or "").lower().strip(),
        "selected": selected,
        "priority": priority,
        "fallback": fallback,
    }


def replay_dc_matches(dcs: list[str], strategy: dict[str, Any]) -> bool:
    av = [str(x).lower() for x in dcs if str(x).strip()]
    if not av:
        return str(strategy.get("mode", "any")) == "any"
    mode = str(strategy.get("mode", "any"))
    selected = [str(x).lower() for x in strategy.get("selected", [])]
    fixed = str(strategy.get("fixed", "")).lower()
    fallback = str(strategy.get("fallback", "wait"))
    if mode == "any":
        return True
    if mode == "fixed":
        return fixed in av
    if mode == "exclude":
        return any(x not in selected for x in av)
    if mode in {"whitelist", "priority"}:
        if any(x in selected for x in av):
            return True
        return fallback == "any"
    return False


def _episode_dc_segments(ep: dict[str, Any]) -> list[tuple[float, float, list[str]]]:
    start = float(ep.get("startEpoch", 0) or 0)
    end = float(ep.get("endEpoch", 0) or 0)
    if start <= 0 or end <= start:
        return []
    current = list(ep.get("startDatacenters", []) or [])
    if not current:
        current = list(ep.get("datacenters", []) or [])
    transitions = []
    for row in ep.get("dcTransitions", []) or []:
        t = _safe_event_epoch(row.get("time"))
        if start < t < end:
            transitions.append((t, list(row.get("datacenters", []) or [])))
    transitions.sort(key=lambda x: x[0])
    out: list[tuple[float, float, list[str]]] = []
    cursor = start
    for t, dcs in transitions:
        if t > cursor:
            out.append((cursor, t, list(current)))
        current = list(dcs)
        cursor = t
    if end > cursor:
        out.append((cursor, end, list(current)))
    return out


def _clip_segments(segments: list[tuple[float, float]], start: float, end: float) -> list[tuple[float, float]]:
    out = []
    for a, b in segments:
        x, y = max(float(a), start), min(float(b), end)
        if y > x:
            out.append((x, y))
    return out


def build_replay_episode(ep: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    start = float(ep.get("startEpoch", 0) or 0)
    end = float(ep.get("endEpoch", 0) or 0)
    timeline = _episode_dc_segments(ep)
    dcs_union = list(ep.get("datacenters", []) or [])
    dc_unknown = not dcs_union and not any(seg[2] for seg in timeline)

    eligible_segments = [(a, b) for a, b, dcs in timeline if replay_dc_matches(dcs, strategy)]
    if not timeline and start > 0 and end > start:
        # Legacy history may lack a DC transition timeline. For 'any' the window
        # is still known to have existed; for DC-specific modes use union evidence.
        if str(strategy.get("mode", "any")) == "any" or (dcs_union and replay_dc_matches(dcs_union, strategy)):
            eligible_segments = [(start, end)]
    dc_eligible = bool(eligible_segments)

    start_poll = _poll_seconds(ep.get("startPollSeconds"))
    end_poll = _poll_seconds(ep.get("endPollSeconds"))
    bounds_known = start_poll is not None and end_poll is not None
    lower_segments = list(eligible_segments)
    upper_segments = list(eligible_segments)
    if bounds_known:
        conservative_end = max(start, end - float(end_poll))
        lower_segments = _clip_segments(eligible_segments, start, conservative_end)
        optimistic_start = start - float(start_poll)
        upper_segments = list(eligible_segments)
        # The pre-detection extension inherits the initial observed DC set.
        initial_dcs = list(ep.get("startDatacenters", []) or dcs_union)
        if replay_dc_matches(initial_dcs, strategy) or str(strategy.get("mode", "any")) == "any":
            upper_segments.append((optimistic_start, start))
        upper_segments.sort(key=lambda x: x[0])

    return {
        "start": ep.get("start"), "end": ep.get("end"),
        "observedSeconds": ep.get("observedSeconds"),
        "datacenters": dcs_union,
        "dcUnknown": dc_unknown, "dcEligible": dc_eligible,
        "nominalSegments": eligible_segments,
        "lowerSegments": lower_segments,
        "upperSegments": upper_segments,
        "boundsKnown": bounds_known,
        "startPollSeconds": start_poll, "endPollSeconds": end_poll,
    }


def _merge_linear_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    rows = sorted((float(a), float(b)) for a, b in intervals if float(b) > float(a))
    if not rows:
        return []
    out = [rows[0]]
    for a, b in rows[1:]:
        pa, pb = out[-1]
        if a <= pb:
            out[-1] = (pa, max(pb, b))
        else:
            out.append((a, b))
    return out


def phase_capture_probability(segments: list[tuple[float, float]], interval_seconds: int) -> float:
    """Exact fraction of polling phases that hit >=1 segment for fixed cadence.

    Phase is uniformly distributed in [0, interval). Each availability segment
    maps to an arc on that phase circle. The union of arcs gives the fraction of
    all possible schedule phases that would observe at least one available point.
    """
    interval = max(1.0, float(interval_seconds))
    arcs: list[tuple[float, float]] = []
    for a, b in segments:
        a, b = float(a), float(b)
        if b <= a:
            continue
        length = b - a
        if length >= interval:
            return 1.0
        start_mod = a % interval
        end_mod = start_mod + length
        if end_mod <= interval:
            arcs.append((start_mod, end_mod))
        else:
            arcs.append((start_mod, interval))
            arcs.append((0.0, end_mod - interval))
    merged = _merge_linear_intervals(arcs)
    covered = sum(b - a for a, b in merged)
    return max(0.0, min(1.0, covered / interval))


def choose_replay_interval(
    rows: list[dict[str, Any]], retain_percent: float = 95.0, max_calls_per_day: int = 0
) -> dict[str, Any] | None:
    if not rows:
        return None
    allowed = [r for r in rows if max_calls_per_day <= 0 or int(r.get("callsPerDayPerTarget", 0)) <= max_calls_per_day]
    if not allowed:
        return {
            "intervalSeconds": None,
            "reason": "No tested interval satisfies the max calls/day limit.",
            "targetCaptureRatePercent": None,
        }
    best = max(float(r.get("nominalCaptureRatePercent", 0) or 0) for r in allowed)
    threshold = best * max(0.0, min(1.0, float(retain_percent) / 100.0))    candidates = [r for r in allowed if float(r.get("nominalCaptureRatePercent", 0) or 0) + 1e-9 >= threshold]
    chosen = max(candidates, key=lambda r: int(r.get("intervalSeconds", 0))) if candidates else max(allowed, key=lambda r: float(r.get("nominalCaptureRatePercent", 0) or 0))
    return {
        "intervalSeconds": int(chosen.get("intervalSeconds")),
        "nominalCaptureRatePercent": float(chosen.get("nominalCaptureRatePercent", 0) or 0),
        "callsPerDayPerTarget": int(chosen.get("callsPerDayPerTarget", 0) or 0),
        "bestNominalCaptureRatePercent": round(best, 1),
        "targetCaptureRatePercent": round(threshold, 1),
        "retainPercent": float(retain_percent),
        "reason": f"Slowest tested interval retaining at least {float(retain_percent):.1f}% of the best nominal capture rate within the call limit.",
    }


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _utc_slot(epoch: float) -> tuple[int, int]:
    dt = datetime.fromtimestamp(float(epoch), timezone.utc)
    return dt.weekday(), dt.hour


def _circular_distance(a: int, b: int, period: int) -> int:
    d = abs(int(a) - int(b))
    return min(d, period - d)


def _slot_density(
    episodes: list[dict[str, Any]], query_epoch: float, now_epoch: float, half_life_days: float
) -> float:
    qwd, qhr = _utc_slot(query_epoch)
    total = 0.0
    for ep in episodes:
        start = float(ep.get("startEpoch", 0) or 0)
        if start <= 0:
            continue
        wd, hr = _utc_slot(start)
        hour_dist = _circular_distance(hr, qhr, 24)
        day_dist = _circular_distance(wd, qwd, 7)
        hour_kernel = math.exp(-0.5 * (hour_dist / 1.6) ** 2)
        day_kernel = math.exp(-0.5 * (day_dist / 1.15) ** 2)
        age_days = max(0.0, (now_epoch - start) / 86400.0)
        recency = math.exp(-math.log(2.0) * age_days / max(1.0, half_life_days))
        total += hour_kernel * day_kernel * recency
    return total


def _slot_peak_density(episodes: list[dict[str, Any]], now_epoch: float, half_life_days: float) -> float:
    if not episodes:
        return 0.0
    base_dt = datetime.fromtimestamp(now_epoch, timezone.utc)
    monday = now_epoch - (base_dt.weekday() * 24 + base_dt.hour) * 3600
    strongest = 0.0
    for wd in range(7):
        for hr in range(24):
            strongest = max(strongest, _slot_density(episodes, monday + (wd * 24 + hr) * 3600, now_epoch, half_life_days))
    return strongest


def _normalized_slot_score(
    episodes: list[dict[str, Any]], query_epoch: float, now_epoch: float, half_life_days: float,
    strongest: float | None = None
) -> float:
    if not episodes:
        return 0.0
    current = _slot_density(episodes, query_epoch, now_epoch, half_life_days)
    peak = _slot_peak_density(episodes, now_epoch, half_life_days) if strongest is None else strongest
    return round(100.0 * current / peak, 1) if peak > 0 else 0.0


def _cadence_stats(episodes: list[dict[str, Any]], query_epoch: float) -> tuple[float, float | None, float | None, int]:
    starts = sorted(float(e.get("startEpoch", 0) or 0) for e in episodes if float(e.get("startEpoch", 0) or 0) > 0)
    gaps = [b - a for a, b in zip(starts, starts[1:]) if b - a >= 60]
    last_start = starts[-1] if starts else None
    elapsed = max(0.0, query_epoch - last_start) if last_start is not None else None
    if not gaps or elapsed is None:
        return 50.0, (statistics.median(gaps) if gaps else None), elapsed, len(gaps)
    median_gap = float(statistics.median(gaps))
    if median_gap <= 0:
        return 50.0, median_gap, elapsed, len(gaps)
    return round(_clamp(100.0 * elapsed / median_gap), 1), median_gap, elapsed, len(gaps)


def _capture_margin_score(episodes: list[dict[str, Any]], poll_seconds: int) -> tuple[float, float | None, int]:
    durations = [
        float(e.get("observedSeconds")) for e in episodes
        if e.get("closed") and e.get("observedSeconds") is not None and not e.get("continuityUncertain")
    ]
    if not durations:
        return 50.0, None, 0
    median_duration = float(statistics.median(durations))
    ratio = median_duration / max(1.0, float(poll_seconds))
    score = 100.0 * (1.0 - math.exp(-ratio / 3.0))
    return round(_clamp(score), 1), median_duration, len(durations)


def _prediction_confidence(episode_count: int, gap_count: int, duration_count: int, continuity_good: int) -> float:
    if episode_count <= 0:
        return 0.0
    sample = 1.0 - math.exp(-episode_count / 8.0)
    gap_factor = 0.55 + 0.45 * min(1.0, gap_count / 5.0)
    duration_factor = 0.70 + 0.30 * min(1.0, duration_count / 5.0)
    continuity_factor = 0.70 + 0.30 * (continuity_good / max(1, episode_count))
    return _clamp(100.0 * sample * gap_factor * duration_factor * continuity_factor)


def build_watch_prediction(
    episodes: list[dict[str, Any]], now_epoch: float, current_poll_seconds: int,
    half_life_days: float, dc_fit_score: float = 100.0, horizon_hours: int = 24
) -> dict[str, Any]:
    episodes = sorted(episodes, key=lambda e: float(e.get("startEpoch", 0) or 0))
    if not episodes:
        return {
            "watchIndex": 0.0, "confidence": 0.0, "band": "样本不足",
            "components": {"slotActivity": 0.0, "cadenceDue": 0.0, "dcFit": dc_fit_score, "captureMargin": 0.0},
            "forecast": [], "nextPeak": None, "sampleCount": 0,
        }

    continuity_good = sum(1 for e in episodes if not e.get("continuityUncertain"))
    capture_score, median_duration, duration_count = _capture_margin_score(episodes, current_poll_seconds)
    cadence_score, median_gap, elapsed, gap_count = _cadence_stats(episodes, now_epoch)
    confidence = _prediction_confidence(len(episodes), gap_count, duration_count, continuity_good)
    slot_peak = _slot_peak_density(episodes, now_epoch, half_life_days)

    def score_at(epoch: float) -> tuple[float, dict[str, float]]:
        slot = _normalized_slot_score(episodes, epoch, now_epoch, half_life_days, slot_peak)
        cadence, _, _, _ = _cadence_stats(episodes, epoch)
        components = {
            "slotActivity": round(slot, 1),
            "cadenceDue": round(cadence, 1),
            "dcFit": round(_clamp(dc_fit_score), 1),
            "captureMargin": round(capture_score, 1),
        }
        raw = (
            0.40 * components["slotActivity"] + 0.30 * components["cadenceDue"] +
            0.15 * components["dcFit"] + 0.15 * components["captureMargin"]
        )
        conf = confidence / 100.0
        index = _clamp(raw * (0.35 + 0.65 * conf))
        return round(index, 1), components

    watch_index, current_components = score_at(now_epoch)
    forecast = []
    for offset in range(max(1, min(72, int(horizon_hours)))):
        epoch = now_epoch + offset * 3600
        idx, comps = score_at(epoch)
        forecast.append({
            "offsetHours": offset,
            "epoch": int(epoch),
            "timeUtc": datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="minutes"),
            "watchIndex": idx,
            "slotActivity": comps["slotActivity"],
            "cadenceDue": comps["cadenceDue"],
        })
    peak = max(forecast, key=lambda x: (float(x.get("watchIndex", 0)), -int(x.get("offsetHours", 0)))) if forecast else None

    if confidence < 25:
        band = "样本不足"
    elif watch_index >= 75:
        band = "高"
    elif watch_index >= 55:
        band = "中高"
    elif watch_index >= 35:
        band = "中"
    else:
        band = "低"

    return {
        "watchIndex": watch_index,
        "confidence": round(confidence, 1),
        "band": band,
        "components": current_components,
        "sampleCount": len(episodes),
        "gapCount": gap_count,
        "durationSampleCount": duration_count,
        "medianGapSeconds": round(median_gap, 1) if median_gap is not None else None,
        "elapsedSinceLastStartSeconds": round(elapsed, 1) if elapsed is not None else None,
        "medianObservedSeconds": round(median_duration, 1) if median_duration is not None else None,
        "halfLifeDays": round(float(half_life_days), 1),
        "weights": {"slotActivity": 0.40, "cadenceDue": 0.30, "dcFit": 0.15, "captureMargin": 0.15},
        "forecast": forecast,
        "nextPeak": peak,
    }


def parse_list_output(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.lower().startswith("plancode") or set(s) <= {"-", " "}:
            continue
        parts = re.split(r"\s{2,}", s)
        if len(parts) < 5:
            continue
        plan_code, category, name = parts[0], parts[1], parts[2]
        m = re.match(r"([0-9]+(?:\.[0-9]+)?)\s+([A-Z]{3})$", parts[3].strip())
        if not m:
            continue
        status = parts[4].strip().lower()
        dcs = split_dcs(parts[5] if len(parts) > 5 else "")
        rows.append({
            "planCode": plan_code,
            "category": category,
            "name": name,
            "price": float(m.group(1)),
            "currency": m.group(2),
            "status": status,
            "datacenters": dcs,
        })
    return rows


def parse_check_output(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.lower().startswith("plancode") or set(s) <= {"-", " "}:
            continue
        parts = re.split(r"\s{2,}", s)
        if len(parts) < 4:
            continue
        status = parts[3].strip().lower()
        if status not in {"available", "unavailable"}:
            continue
        rows.append({
            "planCode": parts[0].strip(),
            "memory": parts[1].strip(),
            "storage": parts[2].strip(),
            "status": status,
            "datacenters": split_dcs(parts[4] if len(parts) >= 5 else ""),
        })
    return rows


def parse_catalog_options_output(text: str) -> dict[str, list[dict[str, Any]]]:
    """Parse `check --list-options` robustly even when description is empty.

    kimsufi-notifier prints: option<TAB>description<TAB>default.  With an empty
    description, tabwriter collapses the visible output to effectively two
    fields: `option ... true`.  Older parsers treated `true` as the description
    and silently set default=False.  Parse the trailing boolean first.
    """
    families: dict[str, list[dict[str, Any]]] = {}
    for line in text.splitlines():
        s = line.rstrip()
        stripped = s.strip()
        if not stripped or stripped.lower().startswith("option") or set(stripped) <= {"-", " "}:
            continue

        # The final column is always the boolean `default`.  Detect it from the
        # right so an empty description does not shift columns.
        m_default = re.search(r"(?:\t+|\s{2,})(true|false)\s*$", s, re.IGNORECASE)
        if m_default:
            default = m_default.group(1).lower() == "true"
            left = s[:m_default.start()].rstrip()
        else:
            # Fallback for unusual rendering with a single space before bool.
            m_default = re.search(r"\s+(true|false)\s*$", s, re.IGNORECASE)
            if not m_default:
                continue
            default = m_default.group(1).lower() == "true"
            left = s[:m_default.start()].rstrip()

        parts = re.split(r"\t+|\s{2,}", left, maxsplit=1)
        option_token = parts[0].strip() if parts else ""
        description = parts[1].strip() if len(parts) >= 2 else ""
        if "=" not in option_token:
            continue
        family, value = option_token.split("=", 1)
        family, value = family.strip(), value.strip()
        if not family or not value:
            continue
        families.setdefault(family, []).append({
            "value": value,
            "description": description,
            "default": default,
        })
    return families

def catalog_option_variants(plan_code: str, families: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    memories = [x.get("value", "") for x in families.get("memory", [])] or [""]
    storages = [x.get("value", "") for x in families.get("storage", [])] or [""]
    rows: list[dict[str, Any]] = []
    for memory in memories:
        for storage in storages:
            rows.append({"planCode": plan_code, "memory": memory, "storage": storage, "status": "unavailable", "datacenters": []})
    return rows


def parse_order_options_output(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.lower().startswith("item-option") or set(s) <= {"-", " "}:
            continue
        parts = re.split(r"\s{2,}|\t+", s, maxsplit=2)
        if len(parts) < 2 or "=" not in parts[0]:
            continue
        family, plan_code = parts[0].split("=", 1)
        price = parts[2].strip() if len(parts) >= 3 else ""
        price_known = bool(price)
        amount, currency = parse_money_text(price) if price_known else (None, "")
        rows.append({
            "family": family.strip(), "planCode": plan_code.strip(),
            "name": parts[1].strip(), "price": price, "amount": amount, "currency": currency,
            "priceKnown": price_known,
        })
    return rows


def _normalize_option_text(value: str) -> str:
    s = str(value or "").lower().replace("×", "x")
    s = s.replace("_", "-")
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9.+-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _plan_suffix_candidates(plan_code: str) -> list[str]:
    plan = _normalize_option_text(plan_code)
    out = []
    if plan:
        out.append(plan)
        out.append(re.sub(r"-v\d+$", "", plan))
    # Keep stable order, remove empties/duplicates.
    return [x for i, x in enumerate(out) if x and x not in out[:i]]


def _strip_known_plan_suffix(value: str, plan_code: str) -> str:
    s = _normalize_option_text(value)
    for suffix in sorted(_plan_suffix_candidates(plan_code), key=len, reverse=True):
        if s.endswith("-" + suffix):
            return s[:-(len(suffix) + 1)]
    return s


def _memory_signature(value: str) -> dict[str, Any]:
    s = _normalize_option_text(value)
    gb = None
    speed = None
    m = re.search(r"(?:ram-)?(\d{1,4})g(?:b)?(?:-|$)", s)
    if not m:
        m = re.search(r"(\d{1,4})-?gb(?:-|$)", s)
    if m:
        gb = int(m.group(1))
    # Prefer explicit MHz; then a 4-digit DDR-ish token.
    m = re.search(r"(\d{3,5})-?mhz", s)
    if not m:
        nums = [int(x) for x in re.findall(r"(?:^|-)(\d{4})(?:-|$)", s)]
        plausible = [x for x in nums if 1000 <= x <= 10000]
        if plausible:
            speed = plausible[-1]
    else:
        speed = int(m.group(1))
    ecc = None
    if "noecc" in s or "non-ecc" in s or "nonecc" in s:
        ecc = False
    elif re.search(r"(?:^|-)ecc(?:-|$)", s):
        ecc = True
    return {"gb": gb, "speed": speed, "ecc": ecc, "text": s}


def _storage_groups(value: str) -> list[dict[str, Any]]:
    s = _normalize_option_text(value)
    # Human labels often normalize to forms like '2-x-4-tb-sata-hdd'.
    # Collapse the separator around x so the compact and human parsers share one grammar.
    s = re.sub(r'(\d+)-x-(\d)', r'\1x\2', s)
    groups: list[dict[str, Any]] = []

    # Compact OVH tokens: 2x4000sa, 2x500nvme, 4x960ssd, etc.
    for m in re.finditer(r"(\d+)x(\d+)(nvme|ssd|sas|sa)(?:-|$)", s):
        count, size, kind = int(m.group(1)), int(m.group(2)), m.group(3)
        media = "nvme" if kind == "nvme" else ("ssd" if kind == "ssd" else ("sas" if kind == "sas" else "hdd"))
        groups.append({"count": count, "gb": size, "media": media})

    # Human text after normalization: 2x4-tb-sata-hdd / 2x500-gb-nvme
    human_pat = re.compile(
        r"(\d+)x(\d+(?:\.\d+)?)-(tb|gb)(?:-[a-z0-9]+){0,3}?-(nvme|ssd|sata|hdd|sas)(?:-|$)"
    )
    for m in human_pat.finditer(s):
        count = int(m.group(1))
        size = float(m.group(2))
        unit = m.group(3)
        media_raw = m.group(4)
        gb = int(round(size * 1000)) if unit == "tb" else int(round(size))
        media = "nvme" if media_raw == "nvme" else ("ssd" if media_raw == "ssd" else ("sas" if media_raw == "sas" else "hdd"))
        cand = {"count": count, "gb": gb, "media": media}
        if cand not in groups:
            groups.append(cand)

    return groups


def _capacity_close(a: int | None, b: int | None) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    # Marketing TB vs decimal GB / near-capacity naming.
    common = {(4000, 4096), (2000, 2048), (1000, 1024), (500, 512)}
    if (a, b) in common or (b, a) in common:
        return True
    return abs(a - b) <= max(10, int(max(a, b) * 0.03))


def _semantic_option_score(
    family: str, generic_value: str, option: dict[str, Any], plan_code: str
) -> tuple[float, list[str]]:
    family = str(family or "")
    generic = _normalize_option_text(generic_value)
    raw_plan = _normalize_option_text(option.get("planCode", ""))
    name = _normalize_option_text(option.get("name", ""))
    combined = "-".join(x for x in (raw_plan, name) if x)
    reasons: list[str] = []
    score = 0.0

    if family == "memory":
        want = _memory_signature(generic)
        got_plan = _memory_signature(_strip_known_plan_suffix(raw_plan, plan_code))
        got_name = _memory_signature(name)
        # Merge candidate facts, preferring planCode then human name.
        got = {
            "gb": got_plan["gb"] if got_plan["gb"] is not None else got_name["gb"],
            "speed": got_plan["speed"] if got_plan["speed"] is not None else got_name["speed"],
            "ecc": got_plan["ecc"] if got_plan["ecc"] is not None else got_name["ecc"],
        }
        if want["gb"] is not None and got["gb"] == want["gb"]:
            score += 100
            reasons.append("memory_gb")
        elif want["gb"] is not None:
            return -1000.0, ["memory_gb_mismatch"]
        if want["ecc"] is not None and got["ecc"] is not None:
            if want["ecc"] == got["ecc"]:
                score += 20
                reasons.append("ecc")
            else:
                score -= 35
                reasons.append("ecc_mismatch")
        if want["speed"] is not None and got["speed"] is not None:
            if want["speed"] == got["speed"]:
                score += 18
                reasons.append("speed")
            else:
                # Speed naming can differ between catalog/option generations.
                score -= min(12, abs(want["speed"] - got["speed"]) / 200.0)
                reasons.append("speed_diff")
        if generic and generic in combined:
            score += 40
            reasons.append("token_contains")

    elif family == "storage":
        want = _storage_groups(generic)
        got = _storage_groups(_strip_known_plan_suffix(raw_plan, plan_code) + "-" + name)
        if not want:
            return -1000.0, ["storage_signature_missing"]
        used = set()
        matched = 0
        for wg in want:
            best = None
            best_score = -1
            for i, gg in enumerate(got):
                if i in used:
                    continue
                s = 0
                if wg["media"] == gg["media"]:
                    s += 50
                elif {wg["media"], gg["media"]} <= {"hdd", "sas"}:
                    s += 25
                else:
                    continue
                if wg["count"] == gg["count"]:
                    s += 30
                else:
                    continue
                if _capacity_close(wg["gb"], gg["gb"]):
                    s += 50
                else:
                    continue
                if s > best_score:
                    best_score = s
                    best = i
            if best is not None:
                used.add(best)
                score += best_score
                matched += 1
        if matched != len(want):
            return -1000.0, [f"storage_groups_{matched}/{len(want)}"]
        reasons.append("storage_signature")
        if generic and generic in combined:
            score += 40
            reasons.append("token_contains")
    else:
        if generic and generic in combined:
            score += 80
            reasons.append("generic_contains")

    return score, reasons


def _family_alias_kind(value: str) -> str:
    f = _normalize_option_text(value)
    if f in {"memory", "ram", "mem", "memory-ram"} or "memory" in f or re.search(r"(?:^|-)ram(?:-|$)", f):
        return "memory"
    if f in {"storage", "disk", "disks", "drive", "drives", "harddisk", "hard-disk"} or "storage" in f or "disk" in f or "drive" in f:
        return "storage"
    return "other"


def find_order_option_match(
    rows: list[dict[str, Any]], family: str, generic_value: str, full_value: str, plan_code: str
) -> tuple[dict[str, Any] | None, str, float, list[str]]:
    family = str(family or "")
    generic = _normalize_option_text(generic_value)
    full = _normalize_option_text(full_value)
    plan = _normalize_option_text(plan_code)
    all_rows = list(rows or [])
    preferred = [x for x in all_rows if _family_alias_kind(str(x.get("family", ""))) == family]
    same_exact = [x for x in all_rows if str(x.get("family", "")) == family]

    if full:
        hit = next((x for x in all_rows if _normalize_option_text(x.get("planCode", "")) == full), None)
        if hit:
            return hit, "full_exact", 1000.0, []

    search_sets: list[tuple[list[dict[str, Any]], str]] = []
    if same_exact:
        search_sets.append((same_exact, "family"))
    if preferred:
        search_sets.append((preferred, "alias_family"))
    search_sets.append((all_rows, "all_families"))
    seen_sets: set[tuple[int, ...]] = set()
    for candidates, label in search_sets:
        sig = tuple(id(x) for x in candidates)
        if sig in seen_sets:
            continue
        seen_sets.add(sig)
        if generic:
            exact_expected = f"{generic}-{plan}" if plan else generic
            hit = next((x for x in candidates if _normalize_option_text(x.get("planCode", "")) == exact_expected), None)
            if hit:
                return hit, f"{label}_generic_plus_plan_exact", 950.0, []
            hit = next((x for x in candidates if _normalize_option_text(x.get("planCode", "")) == generic), None)
            if hit:
                return hit, f"{label}_generic_exact", 940.0, []
            canonical = [x for x in candidates if _strip_known_plan_suffix(str(x.get("planCode", "")), plan_code) == generic]
            if len(canonical) == 1:
                return canonical[0], f"{label}_canonical_suffix", 920.0, []
            contains = [
                x for x in candidates
                if str(x.get("planCode", "")).lower().startswith(generic + "-")
                or generic in str(x.get("planCode", "")).lower()
            ]
            if len(contains) == 1:
                return contains[0], f"{label}_token_contains", 880.0, []

    scored: list[tuple[float, str, dict[str, Any], list[str]]] = []
    for x in all_rows:
        actual_kind = _family_alias_kind(str(x.get("family", "")))
        if actual_kind in {"memory", "storage"} and actual_kind != family:
            continue
        score, reasons = _semantic_option_score(family, generic, x, plan_code)
        if score > 0:
            if actual_kind == family:
                score += 25
                reasons = list(reasons) + ["family_alias"]
            scored.append((score, str(x.get("planCode", "")), x, reasons))
    if not scored:
        return None, ("family_missing" if not preferred else "no_match"), 0.0, []
    scored.sort(key=lambda t: (-t[0], len(t[1]), t[1]))
    top = scored[0]
    threshold = 90.0 if family == "memory" else 120.0 if family == "storage" else 70.0
    if top[0] < threshold:
        return None, "semantic_below_threshold", top[0], top[3]
    if len(scored) > 1 and abs(top[0] - scored[1][0]) < 8.0:
        return None, "semantic_ambiguous", top[0], top[3]
    method = "semantic_cross_family" if _family_alias_kind(str(top[2].get("family", ""))) != family else "semantic"
    return top[2], method, top[0], top[3]

def find_order_option_row(
    rows: list[dict[str, Any]], family: str, generic_value: str, full_value: str, plan_code: str
) -> dict[str, Any] | None:
    match, _, _, _ = find_order_option_match(rows, family, generic_value, full_value, plan_code)
    return match

def price_duration_months(duration: str) -> float | None:
    s = str(duration or "").upper().strip()
    m = re.fullmatch(r"P(?:(\d+)Y)?(?:(\d+)M)?", s)
    if not m:
        return None
    years = int(m.group(1) or 0)
    months = int(m.group(2) or 0)
    total = years * 12 + months
    return float(total) if total > 0 else None


def price_profile_sort_key(duration: str, mode: str) -> tuple[float, str, str]:
    months = price_duration_months(duration)
    return (months if months is not None else 99999.0, str(duration), str(mode))


def parse_price_output(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.lower().startswith("price-duration") or set(s) <= {"-", " "}:
            continue
        parts = re.split(r"\s{2,}|\t+", s, maxsplit=3)
        if len(parts) < 3:
            continue
        duration, mode, price = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not duration.startswith("P") or not mode:
            continue
        amount, currency = parse_money_text(price)
        rows.append({
            "duration": duration,
            "mode": mode,
            "price": price,
            "amount": amount,
            "currency": currency,
            "description": parts[3].strip() if len(parts) >= 4 else "",
        })
    return rows


def money_value(price: dict[str, Any] | None) -> float | None:
    if not isinstance(price, dict):
        return None
    value = price.get("value")
    try:
        if value is not None:
            return float(value)
    except (TypeError, ValueError):
        pass
    micro = price.get("priceInUcents")
    try:
        if micro is not None:
            return float(micro) / 100000000.0
    except (TypeError, ValueError):
        pass
    text = str(price.get("text", "") or "").strip()
    if not text:
        return None
    amount, _ = parse_money_text(text)
    return amount


def option_price_for_config(option: dict[str, Any], duration: str, mode: str) -> dict[str, Any] | None:
    for p in option.get("prices", []) or []:
        if str(p.get("duration", "")) == str(duration) and str(p.get("mode", p.get("pricingMode", ""))) == str(mode):
            amount = p.get("amount")
            if amount is None and isinstance(p.get("price"), dict):
                amount = money_value(p.get("price"))
            if amount is None:
                return None
            currency = str(p.get("currency", "") or "")
            if not currency and isinstance(p.get("price"), dict):
                currency = str(p["price"].get("currencyCode", "") or "")
            return {
                "duration": str(p.get("duration", "")),
                "mode": str(p.get("mode", p.get("pricingMode", ""))),
                "amount": float(amount),
                "currency": currency.upper(),
                "text": str(p.get("text", "") or (p.get("price", {}) or {}).get("text", "") or ""),
            }
    return None


def cheapest_mandatory_option(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    priced: list[tuple[float, str, dict[str, Any]]] = []
    for row in rows:
        p = option_price_for_config(row, "P1M", "default")
        if p is None:
            continue
        priced.append((float(p.get("amount", 0) or 0), str(row.get("planCode", "")), row))
    if not priced:
        return None
    priced.sort(key=lambda x: (x[0], x[1]))
    return priced[0][2]


def parse_money_text(text: str) -> tuple[float, str]:
    s = str(text or "").strip()
    if not s:
        return 0.0, ""
    # Accept both "307.69 EUR" and OVH-localized forms such as "307.69 €".
    compact = s.replace("\u00a0", " ").replace(" ", "")
    symbol_map = {"€": "EUR", "$": "USD", "£": "GBP", "C$": "CAD"}
    currency = ""
    for sym, code in sorted(symbol_map.items(), key=lambda x: -len(x[0])):
        if sym in compact:
            currency = code
            compact = compact.replace(sym, "")
            break
    mcode = re.search(r"([A-Za-z]{3})", compact)
    if mcode:
        currency = mcode.group(1).upper()
        compact = compact.replace(mcode.group(1), "")
    # Handle comma decimal when no dot is present; otherwise commas are thousands separators.
    if "," in compact and "." not in compact:
        compact = compact.replace(",", ".")
    else:
        compact = compact.replace(",", "")
    m = re.search(r"[-+]?[0-9]+(?:\.[0-9]+)?", compact)
    if not m:
        return 0.0, currency
    try:
        return float(m.group(0)), currency
    except ValueError:
        return 0.0, currency


def compute_budget_reserve(
    base_amount: float, buffer_percent: float = 0.0,
    buffer_fixed: float = 0.0, override: float = 0.0
) -> float:
    override = max(0.0, float(override or 0))
    if override > 0:
        return round(override, 2)
    base = max(0.0, float(base_amount or 0))
    pct = max(0.0, min(500.0, float(buffer_percent or 0)))
    fixed = max(0.0, float(buffer_fixed or 0))
    return round(base * (1.0 + pct / 100.0) + fixed, 2)


def split_dcs(text: str) -> list[str]:
    return [x.strip().lower() for x in text.split(",") if x.strip()]


def parse_ram_gb(token: str) -> int | None:
    m = re.search(r"ram-(\d+)g", token.lower())
    return int(m.group(1)) if m else None


def humanize_memory(token: str) -> str:
    gb = parse_ram_gb(token)
    if gb is None:
        return token or "—"
    extras = []
    low = token.lower()
    if "ecc" in low and "noecc" not in low:
        extras.append("ECC")
    m = re.search(r"-(\d{3,4})(?:-|$)", low)
    if m:
        extras.append(m.group(1) + " MHz")
    return f"{gb} GB" + (f" {' '.join(extras)}" if extras else "")


def disk_types(token: str) -> list[str]:
    low = token.lower()
    out = []
    if "nvme" in low:
        out.append("nvme")
    if "ssd" in low:
        out.append("ssd")
    if re.search(r"\d+(?:sa|sas)(?:-|$)", low) or "hdd" in low:
        out.append("hdd")
    return out or (["other"] if token else [])


def humanize_storage(token: str) -> str:
    if not token:
        return "—"
    low = token.lower()
    m = re.search(r"(\d+)x(\d+)(nvme|ssd|sas|sa)", low)
    if not m:
        return token
    count = int(m.group(1))
    size = int(m.group(2))
    kind = m.group(3)
    if size >= 1000 and size % 1000 == 0:
        size_label = f"{size // 1000} TB"
    else:
        size_label = f"{size} GB"
    type_label = {"nvme": "NVMe", "ssd": "SATA SSD", "sa": "HDD", "sas": "SAS HDD"}[kind]
    return f"{count}×{size_label} {type_label}"


def resolve_full_option(output: str, kind: str, base: str, plan: str) -> str:
    if not base:
        return ""
    exact = f"{base}-{plan}"
    if exact in output:
        return exact

    candidates = set()
    # Accept tokens after "memory=" / "storage=" as well as bare values.
    for line in output.splitlines():
        for m in re.finditer(rf"(?:{re.escape(kind)}\s*=\s*)?([A-Za-z0-9][A-Za-z0-9._-]+)", line):
            tok = m.group(1)
            if base in tok and tok != base:
                candidates.add(tok)
    preferred = [x for x in candidates if x.startswith(base + "-")]
    if preferred:
        preferred.sort(key=lambda x: (0 if plan in x else 1, len(x), x))
        return preferred[0]
    return ""


def format_command(args: list[str]) -> str:
    # Do not include OVH secrets: they are environment variables, not command args.
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return " ".join(shlex.quote(x) for x in args)


class Handler(BaseHTTPRequestHandler):
    commander: Commander
    server_version = "KimsufiStockCommander/0.21"
    auth_enabled: bool = False
    read_token: str = ""
    admin_token: str = ""
    legacy_token: str = ""
    auth_mode: str = "disabled"
    read_token_env: str = "KSC_READ_TOKEN"
    admin_token_env: str = "KSC_ADMIN_TOKEN"
    legacy_token_env: str = "KSC_API_TOKEN"

    READ_CAPABILITIES = (
        "state:read",
        "notifications:read",
        "analytics:read",
        "pricing:read",
        "replay:run",
        "optimizer:run",
    )
    ADMIN_CAPABILITIES = READ_CAPABILITIES + (
        "settings:write",
        "runtime:control",
        "notifications:test",
        "targets:write",
        "pricing:scan",
        "orders:control",
        "automation:control",
    )

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _presented_bearer_token(self) -> str:
        raw = str(self.headers.get("Authorization") or "")
        m = re.match(r"^\s*Bearer\s+(.+?)\s*$", raw, flags=re.IGNORECASE)
        return m.group(1) if m else ""

    @staticmethod
    def _token_matches(presented: str, expected: str) -> bool:
        if not presented or not expected:
            return False
        return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))

    def _auth_role(self) -> str:
        if not self.auth_enabled:
            return "admin"
        presented = self._presented_bearer_token()
        # Admin is tested first deliberately.  Equal read/admin tokens are rejected
        # at startup, but this ordering also preserves legacy single-token semantics.
        if self._token_matches(presented, self.admin_token):
            return "admin"
        if self._token_matches(presented, self.read_token):
            return "read"
        if self._token_matches(presented, self.legacy_token):
            return "admin"
        return ""

    def _capabilities_for_role(self, role: str) -> list[str]:
        if role == "admin":
            return list(self.ADMIN_CAPABILITIES)
        if role == "read":
            return list(self.READ_CAPABILITIES)
        return []

    def _admin_available(self) -> bool:
        return (not self.auth_enabled) or bool(self.admin_token or self.legacy_token)

    def _send_unauthorized(self) -> None:
        data = json.dumps({
            "error": "authentication_required",
            "authRequired": True,
            "authenticated": False,
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("WWW-Authenticate", 'Bearer realm="KimsufiStockCommander"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_forbidden(self, capability: str) -> None:
        self._send_json({
            "error": "admin_required",
            "authenticated": True,
            "role": "read",
            "requiredRole": "admin",
            "requiredCapability": capability,
            "adminAvailable": self._admin_available(),
            "authMode": self.auth_mode,
        }, HTTPStatus.FORBIDDEN)

    def _require_authenticated(self) -> str:
        role = self._auth_role()
        if role:
            return role
        self._send_unauthorized()
        return ""

    def _require_capability(self, capability: str) -> str:
        role = self._require_authenticated()
        if not role:
            return ""
        if capability in self._capabilities_for_role(role):
            return role
        self._send_forbidden(capability)
        return ""

    def _send_json(self, obj: Any, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, text: str, content_type: str = "text/plain; charset=utf-8", filename: str | None = None) -> None:
        data = text.encode("utf-8-sig" if "csv" in content_type else "utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or "0")
        if n > 2_000_000:
            raise ValueError("request too large")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _serve_file(self, rel: str) -> None:
        rel = rel.lstrip("/") or "index.html"
        path = (self.commander.ui / rel).resolve()
        if self.commander.ui.resolve() not in path.parents and path != self.commander.ui.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime = "text/plain; charset=utf-8"
        if path.suffix == ".html":
            mime = "text/html; charset=utf-8"
        elif path.suffix == ".js":
            mime = "application/javascript; charset=utf-8"
        elif path.suffix == ".css":
            mime = "text/css; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        p = urlparse(self.path)
        if p.path == "/api/health":
            role = self._auth_role()
            if self.auth_enabled and not role:
                self._send_json({
                    "ok": True,
                    "version": APP_VERSION,
                    "authRequired": True,
                    "authenticated": False,
                })
            else:
                health = dict(self.commander.health_status())
                health.update({
                    "authRequired": bool(self.auth_enabled),
                    "authenticated": bool(role),
                    "role": role or "admin",
                    "capabilities": self._capabilities_for_role(role or "admin"),
                    "adminAvailable": self._admin_available(),
                    "authMode": self.auth_mode,
                })
                self._send_json(health)
            return
        if p.path == "/api/auth/check":
            role = self._require_authenticated()
            if not role:
                return
            self._send_json({
                "ok": True,
                "version": APP_VERSION,
                "authRequired": bool(self.auth_enabled),
                "authenticated": True,
                "role": role,
                "capabilities": self._capabilities_for_role(role),
                "adminAvailable": self._admin_available(),
                "authMode": self.auth_mode,
            })
            return

        get_caps = {
            "/api/state": "state:read",
            "/api/notifications": "notifications:read",
            "/api/analytics": "analytics:read",
            "/api/analytics/export": "analytics:read",
        }
        if p.path.startswith("/api/"):
            cap = get_caps.get(p.path, "state:read")
            if not self._require_capability(cap):
                return

        if p.path == "/api/state":
            self._send_json(self.commander.snapshot())
            return
        if p.path == "/api/notifications":
            self._send_json({"notifications": list(reversed(self.commander.notification_history[-50:]))})
            return
        if p.path in {"/api/analytics", "/api/analytics/export"}:
            qs = parse_qs(p.query or "")
            target_id = str((qs.get("targetId") or [""])[0])
            try:
                lookback_days = int((qs.get("lookbackDays") or ["30"])[0])
            except ValueError:
                lookback_days = 30
            lookback_days = max(0, min(3650, lookback_days))
            if p.path == "/api/analytics":
                self._send_json(self.commander.stock_analytics(target_id, lookback_days))
            else:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                self._send_text(
                    self.commander.export_stock_analytics_csv(target_id, lookback_days),
                    "text/csv; charset=utf-8",
                    f"kimsufi-stock-history-{stamp}.csv",
                )
            return
        if p.path.startswith("/api/"):
            self._send_json({"error": "not found"}, 404)
            return
        self._serve_file(p.path)

    def do_POST(self) -> None:
        p = urlparse(self.path)
        # Authenticate before reading/parsing the request body.  This preserves
        # v0.20's fail-closed boundary even though v0.21 chooses a more specific
        # capability after inspecting the already-authenticated payload.
        role = ""
        if p.path.startswith("/api/"):
            role = self._require_authenticated()
            if not role:
                return
        try:
            payload = self._read_json()
            if p.path == "/api/settings":
                cap = "automation:control" if "autoOrderArmed" in payload else "settings:write"
            else:
                post_caps = {
                    "/api/catalog/refresh": "runtime:control",
                    "/api/poll": "runtime:control",
                    "/api/notifications/test": "notifications:test",
                    "/api/replay": "replay:run",
                    "/api/replay/portfolio": "replay:run",
                    "/api/optimizer": "optimizer:run",
                    "/api/target": "targets:write",
                    "/api/target/remove": "targets:write",
                    "/api/target/resolve-options": "targets:write",
                    "/api/prices": "pricing:read",
                    "/api/prices/scan": "pricing:scan",
                    "/api/prices/config-scan": "pricing:scan",
                    "/api/pricing/diagnostics": "pricing:read",
                    "/api/target/clear-review": "orders:control",
                }
                cap = post_caps.get(p.path, "state:read")
            if p.path.startswith("/api/") and cap not in self._capabilities_for_role(role):
                self._send_forbidden(cap)
                return

            if p.path == "/api/settings":
                self._send_json({"settings": self.commander.update_settings(payload)})
            elif p.path == "/api/catalog/refresh":
                self.commander.catalog_refresh_event.set()
                self._send_json({"accepted": True}, 202)
            elif p.path == "/api/poll":
                self.commander.force_poll_event.set()
                self._send_json({"accepted": True}, 202)
            elif p.path == "/api/notifications/test":
                item = self.commander.notify_event("TEST", note=str(payload.get("message") or "通知链测试成功"), force=True)
                self._send_json({"ok": True, "notification": item})
            elif p.path == "/api/replay":
                self._send_json(self.commander.strategy_replay(payload))
            elif p.path == "/api/replay/portfolio":
                self._send_json(self.commander.portfolio_replay(payload))
            elif p.path == "/api/optimizer":
                self._send_json(self.commander.optimize_strategies(payload))
            elif p.path == "/api/target":
                self._send_json({"target": self.commander.upsert_target(payload)})
            elif p.path == "/api/target/remove":
                self.commander.remove_target(str(payload.get("id", "")))
                self._send_json({"ok": True})
            elif p.path == "/api/target/resolve-options":
                self._send_json({"target": self.commander.resolve_order_options(str(payload.get("id", "")))})
            elif p.path == "/api/prices":
                plan = str(payload.get("planCode", "")).strip()
                self._send_json({"planCode": plan, "prices": self.commander.list_prices(plan, force=True)})
            elif p.path == "/api/prices/scan":
                self._send_json(self.commander.scan_payment_profiles(force=bool(payload.get("force", True))))
            elif p.path == "/api/prices/config-scan":
                self._send_json(self.commander.scan_configuration_prices(force=bool(payload.get("force", True))))
            elif p.path == "/api/pricing/diagnostics":
                self._send_json(self.commander.pricing_diagnostics(str(payload.get("id", "")), force=bool(payload.get("force", False))))
            elif p.path == "/api/target/clear-review":
                self._send_json({"target": self.commander.clear_review(str(payload.get("id", "")))})
            else:
                self._send_json({"error": "not found"}, 404)
        except (ValueError, KeyError) as e:
            self._send_json({"error": str(e)}, 400)
        except Exception:
            self.commander.log_error(f"HTTP {p.path}: {traceback.format_exc()}")
            self._send_json({"error": "internal error; see data/errors.log"}, 500)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Kimsufi Stock Commander")
    p.add_argument("--host", default="127.0.0.1", help="bind address; keep loopback unless you add your own access control")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--open-browser", action="store_true")
    p.add_argument("--demo", action="store_true", help="use built-in fake catalog; checkout is always blocked")
    return p




def _global_instance_lock_path() -> Path:
    """Per-user lock outside the extracted app folder.

    Prevents two v0.18+ copies from different directories from auto-ordering
    against the same account at the same time.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.environ.get("TEMP")
    if base:
        return Path(base) / "KimsufiStockCommander" / "global-instance.lock"
    return Path.home() / ".kimsufi-stock-commander" / "global-instance.lock"

def _server_security_config(root: Path) -> dict[str, Any]:
    raw = load_json(root / "KimsufiServer.json", {})
    return {
        "authEnabled": bool(raw.get("authEnabled", False)),
        # v0.21 capability tokens.  authTokenEnv remains as the legacy single-
        # token fallback so v0.20 installations upgrade without breaking.
        "readTokenEnv": str(raw.get("readTokenEnv", "KSC_READ_TOKEN") or "KSC_READ_TOKEN").strip(),
        "adminTokenEnv": str(raw.get("adminTokenEnv", "KSC_ADMIN_TOKEN") or "KSC_ADMIN_TOKEN").strip(),
        "authTokenEnv": str(raw.get("authTokenEnv", "KSC_API_TOKEN") or "KSC_API_TOKEN").strip(),
    }


def main() -> int:
    args = build_arg_parser().parse_args()
    root = Path(__file__).resolve().parent
    security = _server_security_config(root)
    auth_enabled = bool(security.get("authEnabled"))
    read_token_env = str(security.get("readTokenEnv") or "KSC_READ_TOKEN")
    admin_token_env = str(security.get("adminTokenEnv") or "KSC_ADMIN_TOKEN")
    legacy_token_env = str(security.get("authTokenEnv") or "KSC_API_TOKEN")
    read_token = str(os.environ.get(read_token_env, "") or "")
    admin_token = str(os.environ.get(admin_token_env, "") or "")
    legacy_token = str(os.environ.get(legacy_token_env, "") or "")
    auth_mode = "disabled"
    if auth_enabled:
        # If either new capability token is configured, v0.21 mode is active and
        # the legacy token is intentionally ignored.  Otherwise KSC_API_TOKEN is
        # preserved as a single Admin token for backwards compatibility.
        using_capability_tokens = bool(read_token or admin_token)
        for env_name, token in ((read_token_env, read_token), (admin_token_env, admin_token)):
            if token and len(token) < 24:
                print(f"API token environment variable {env_name!r} is too short (minimum 24 characters). Refusing to start.", file=sys.stderr)
                return 3
        if using_capability_tokens:
            legacy_token = ""
            if read_token and admin_token and hmac.compare_digest(read_token.encode("utf-8"), admin_token.encode("utf-8")):
                print("KSC_READ_TOKEN and KSC_ADMIN_TOKEN must be different values. Refusing to start.", file=sys.stderr)
                return 3
            if read_token and admin_token:
                auth_mode = "read-admin"
            elif read_token:
                auth_mode = "read-only"
            else:
                auth_mode = "admin-only"
        else:
            if len(legacy_token) < 24:
                print(
                    f"API authentication is enabled but none of {read_token_env!r}, {admin_token_env!r}, "
                    f"or legacy {legacy_token_env!r} contains a valid token (minimum 24 characters). Refusing to start.",
                    file=sys.stderr,
                )
                return 3
            auth_mode = "legacy-single"

    # Hold both a folder-local lock and a per-user global lock for the full
    # process lifetime.  The global lock prevents two v0.18+ copies extracted in
    # different directories from maintaining separate receipts and buying the
    # same target independently.
    instance_lock = None
    global_instance_lock = None
    try:
        instance_lock = ExclusiveFileLock(root / "data" / "instance.lock", nonblocking=True)
        instance_lock.__enter__()
        global_instance_lock = ExclusiveFileLock(_global_instance_lock_path(), nonblocking=True)
        global_instance_lock.__enter__()
    except OSError:
        if instance_lock is not None:
            try:
                instance_lock.__exit__(None, None, None)
            except Exception:
                pass
        print("Another Kimsufi Stock Commander v0.18+ instance is already running for this user.", file=sys.stderr)
        return 2

    commander = Commander(root, demo=args.demo)
    Handler.commander = commander
    Handler.auth_enabled = auth_enabled
    Handler.read_token = read_token if auth_enabled else ""
    Handler.admin_token = admin_token if auth_enabled else ""
    Handler.legacy_token = legacy_token if auth_enabled else ""
    Handler.auth_mode = auth_mode
    Handler.read_token_env = read_token_env
    Handler.admin_token_env = admin_token_env
    Handler.legacy_token_env = legacy_token_env
    commander.start_worker()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    url = f"http://{args.host}:{args.port}/"
    print(f"Kimsufi Stock Commander v{APP_VERSION}")
    print(f"Dashboard: {url}")
    print(f"Mode: {'DEMO (checkout blocked)' if args.demo else 'LIVE backend (auto-order remains disarmed until enabled in UI)'}")
    print(f"API auth: {'ENABLED (' + auth_mode + ')' if auth_enabled else 'disabled'}")
    if args.open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        commander.stop_event.set()
        server.shutdown()
        server.server_close()
        if global_instance_lock is not None:
            global_instance_lock.__exit__(None, None, None)
        if instance_lock is not None:
            instance_lock.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())