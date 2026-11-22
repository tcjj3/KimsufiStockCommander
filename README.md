# Kimsufi Stock Commander

**Kimsufi Stock Commander** is a Windows-first visual dashboard for Kimsufi stock monitoring, exact configuration tracking, pricing, datacenter strategy, notifications, historical analysis, replay/optimization, and conservative automatic ordering.

> **Current public release:** v0.19.0  
> **License:** MIT  
> **Status:** independent community project; not affiliated with OVHcloud/Kimsufi.

[简体中文说明](README.zh-CN.md)

## ⚠️ Read this before enabling auto-order

Auto-ordering can create **real paid orders**.

The project is deliberately fail-closed, but you are still responsible for validating your target configuration, quantity, datacenter policy, billing period and budget before arming it.

Recommended first run:

1. run DEMO mode;
2. verify the detected plans/options and pricing;
3. configure a finite budget;
4. use `desiredQty = 1` for the first live target;
5. only then arm the global auto-order switch.

v0.19 does **not** include Web/API authentication. The server binds to `127.0.0.1` by default. If you expose it to a LAN, use a trusted network and restrict the port with Windows Firewall. Do **not** expose v0.19 directly to the public Internet.

## Features

- Dynamic Kimsufi catalog discovery through `kimsufi-notifier`.
- Exact RAM/storage configuration monitoring.
- Filters for RAM, NVMe/SATA SSD/HDD, availability and price range.
- Datacenter strategies:
  - any;
  - fixed;
  - allow-list;
  - priority + fallback;
  - exclusions.
- Billing profile discovery (`price-duration` / `price-mode`).
- Configuration-level monthly and billing-period price estimation using OVH public cart pricing data.
- Separation of **availability** from **orderability** so availability-only/ghost configurations are not treated as purchasable.
- Conservative auto-order workflow:
  - one server per order attempt;
  - final desired quantity tracking;
  - only explicit `order completed:` confirmation counts as success;
  - ambiguous results enter persistent `REVIEW`;
  - persistent active markers / receipts;
  - process/order locks;
  - finite-budget requirement for unattended auto-pay.
- Windows tray client and current-user autostart helpers.
- Remote Tray/backend decoupling (host/port/start/stop behavior are independent).
- Windows desktop and Telegram notifications.
- Stock history and restock-window analytics.
- Watch Index + confidence estimate.
- Historical replay of polling/DC strategies.
- Portfolio replay for priority/budget competition.
- Pareto strategy optimizer.
- Standard-library-only Python backend; no pip dependencies.

## Requirements

- Windows 10/11 or Windows Server.
- Python 3.10+.
- [`TheoBrigitte/kimsufi-notifier`](https://github.com/TheoBrigitte/kimsufi-notifier) installed separately.
- For real ordering, OVH API credentials exposed to the Commander process as environment variables:
  - `OVH_APP_KEY`
  - `OVH_APP_SECRET`
  - `OVH_CONSUMER_KEY`

The default CLI location is:

```text
%USERPROFILE%\go\bin\kimsufi-notifier.exe
```

It can be changed from the UI.

## Quick start

### DEMO mode

Double-click:

```text
start-demo.cmd
```

or run:

```powershell
py -3 .\KimsufiStockCommander.py --demo --open-browser
```

DEMO mode never performs a paid checkout.

### Live mode

Double-click:

```text
start-live.cmd
```

Default dashboard:

```text
http://127.0.0.1:8765/
```

The global auto-order switch starts disarmed.

## Live ordering credentials

Example for the current PowerShell process:

```powershell
$env:OVH_APP_KEY="..."
$env:OVH_APP_SECRET="..."
$env:OVH_CONSUMER_KEY="..."
py -3 .\KimsufiStockCommander.py --open-browser
```

Do not place real credentials in source files or JSON configuration.

## Safety model

The auto-order state machine is intentionally conservative:

```text
AVAILABLE
  ↓
orderability / DC / billing / configuration price / finite budget checks
  ↓
persistent active marker
  ↓
quantity=1 checkout
  ↓
explicit "order completed:"
  ├─ yes → receipt → completedQty + 1
  └─ uncertain → REVIEW / no automatic retry
```

`desiredQty` means the final desired count. If `desiredQty = 3`, Commander submits at most one new order per confirmed success rather than requesting quantity 3 in a single ambiguous transaction.

For unattended `--auto-pay`, the default policy requires a finite total budget. If the budget gate is disabled or unlimited, unattended automatic payment is blocked.

## Availability vs orderability

OVH/Kimsufi availability data can contain configurations that are not present in the current order/cart option set.

v0.19 treats these separately:

- **Orderable**: configuration can currently be represented in the cart/order option set.
- **Availability-only**: availability reports the configuration, but it cannot currently be constructed for ordering.
- **Unknown**: orderability metadata has not been resolved yet.

Availability-only rows may still be observed, but they are not eligible for auto-order.

## Pricing

The catalog base price is not assumed to be the complete configuration price.

Commander uses OVH public cart pricing data to estimate:

- `P1M/default` configuration monthly price;
- the selected billing-period configuration price;
- memory/storage/other mandatory option additions when available.

Displayed prices are estimates before tax unless OVH explicitly provides otherwise. Setup fees and other one-time charges that are not exposed by the queried data must be covered by the configured percentage/fixed budget buffer.

## Tray: local and remote modes

`KimsufiTray.json` controls the Tray independently from the backend.

Local example:

```json
{
  "serverScheme": "http",
  "serverHost": "127.0.0.1",
  "serverPort": 8765,
  "startBackend": true,
  "backendBindHost": "127.0.0.1",
  "backendPort": 8765,
  "allowStopBackend": true
}
```

Remote-client example: see `KimsufiTray.remote-example.json`.

When using a remote backend, normally set:

```json
{
  "serverHost": "192.0.2.10",
  "serverPort": 8765,
  "startBackend": false,
  "allowStopBackend": false
}
```

`192.0.2.10` is a documentation-only example address; replace it with your own trusted host.

### Remote-mode security warning

v0.19 has no Web/API authentication. If the backend listens on `0.0.0.0`, restrict the TCP port to trusted management hosts with Windows Firewall. Do not expose it directly to untrusted networks.

## Notifications

Supported notification channels:

- Windows Tray balloon notifications;
- Telegram Bot API.

Telegram token is read from an environment variable (default `KSC_TELEGRAM_BOT_TOKEN`) rather than stored in settings JSON.

## Data directory

Runtime state is written under `data/` and intentionally ignored by Git:

```text
data/
  settings.json
  targets.json
  catalog-cache.json
  stock-history.jsonl
  events.csv
  orders.log
  errors.log
  budget-ledger.json
  locks/
  receipts/
```

Do not commit a populated `data/` directory. It may contain operational metadata and order history.

## Tests

The repository includes regression tests for pricing, option mapping, orderability, order safety, state snapshots, UI pending state, replay/optimizer behavior and Tray/backend decoupling.

Run all Python tests from the repository root:

```powershell
Get-ChildItem .\tests\test_*.py | ForEach-Object { py -3 $_.FullName }
```

Optionally validate frontend JavaScript with Node.js:

```powershell
node --check .\ui\app.js
```

## Legacy watcher

`legacy/watch-kimsufi-autoorder-P1Y.ps1` is retained for historical/reference purposes only.

In the public repository its auto-order flag is **disabled by default**. Prefer Stock Commander for live use.

## Third-party dependency

This project invokes `TheoBrigitte/kimsufi-notifier` as an external executable. It is not bundled here and is distributed by its upstream author under the MIT License. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

MIT. See [LICENSE](LICENSE).
