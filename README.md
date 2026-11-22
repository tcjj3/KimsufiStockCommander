# Kimsufi Stock Commander

**Kimsufi Stock Commander** is a Windows-first visual dashboard for Kimsufi stock monitoring, exact configuration tracking, pricing, datacenter strategy, notifications, historical analysis, replay/optimization, and conservative automatic ordering.

> **Current public release:** v0.20.3  
> **License:** MIT  
> **Status:** independent community project; not affiliated with OVHcloud/Kimsufi.

[简体中文说明](README.zh-CN.md)

## ⚠️ Read this before enabling auto-order

Auto-ordering can create **real paid orders**.

Recommended first run:

1. run DEMO mode;
2. verify the detected plans/options and pricing;
3. configure a finite budget;
4. use `desiredQty = 1` for the first live target;
5. only then arm the global auto-order switch.

The project is deliberately fail-closed, but you are still responsible for validating the exact configuration, quantity, datacenter policy, billing profile and budget.

## Highlights

- Dynamic Kimsufi catalog discovery through `kimsufi-notifier`.
- Exact RAM/storage configuration monitoring.
- Filters for RAM, NVMe/SATA SSD/HDD, availability, orderability and price range.
- Datacenter strategies: any, fixed, allow-list, priority + fallback, exclusions.
- Dynamic billing profile discovery (`price-duration` / `price-mode`).
- Configuration-level monthly and billing-period price estimation using OVH public cart pricing data.
- Separation of **availability** from **orderability**, so availability-only/ghost configurations are not treated as purchasable.
- Conservative auto-order workflow:
  - one server per order attempt;
  - final desired quantity tracking;
  - only explicit `order completed:` confirmation counts as success;
  - ambiguous results enter persistent `REVIEW`;
  - persistent active markers / receipts;
  - process/order locks;
  - finite-budget requirement for unattended auto-pay.
- Windows Tray client and current-user autostart helpers.
- Remote Tray/backend decoupling with independent host/port/start/stop behavior.
- Windows desktop and Telegram notifications.
- Stock history, Watch Index, historical replay and Pareto strategy optimization.
- **v0.20+: optional Web/API Bearer Token authentication** for remote browser/Tray access.
- Standard-library-only Python backend; no pip dependencies.

## Requirements

- Windows 10/11 or Windows Server.
- Python 3.10+.
- [`TheoBrigitte/kimsufi-notifier`](https://github.com/TheoBrigitte/kimsufi-notifier) installed separately.
- For real ordering, OVH API credentials exposed to the Commander process as environment variables:
  - `OVH_APP_KEY`
  - `OVH_APP_SECRET`
  - `OVH_CONSUMER_KEY`

Default CLI location:

```text
%USERPROFILE%\go\bin\kimsufi-notifier.exe
```

It can be changed from the UI.

## Quick start

### DEMO mode

```text
start-demo.cmd
```

or:

```powershell
py -3 .\KimsufiStockCommander.py --demo --open-browser
```

DEMO mode never performs a paid checkout.

### Live mode

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

## Web/API authentication (v0.20+)

Authentication is **disabled by default** for local-only use.

For remote browser/Tray access, generate a strong token:

```powershell
.\generate-api-token.ps1 -SetUserEnvironment
```

Then enable authentication in `KimsufiServer.json`:

```json
{
  "bindHost": "0.0.0.0",
  "port": 8765,
  "openBrowser": true,
  "authEnabled": true,
  "authTokenEnv": "KSC_API_TOKEN"
}
```

Restart Commander after setting the environment variable.

If `authEnabled=true` but the configured environment variable is missing or too short, Commander refuses to start instead of silently falling back to unauthenticated mode.

### Browser authentication

The Web UI prompts for the Bearer Token when authentication is required.

- default storage is session-only;
- “remember this browser” stores the token in `localStorage`;
- authenticated users can replace the token or log out and clear the local token;
- the auth dialog distinguishes no-token, stale-token and authenticated states;
- token input is password-masked by default;
- the HTTP warning is dynamic and disappears automatically when the page is accessed through HTTPS.

### Tray authentication

`KimsufiTray.json` can read the same token from an environment variable:

```json
{
  "apiTokenEnv": "KSC_API_TOKEN"
}
```

Remote Tray clients should normally use:

```json
{
  "serverScheme": "http",
  "serverHost": "192.0.2.10",
  "serverPort": 8765,
  "startBackend": false,
  "allowStopBackend": false,
  "apiTokenEnv": "KSC_API_TOKEN"
}
```

See `KimsufiTray.remote-auth-example.json` and `KimsufiServer.secure-remote-example.json`.

### HTTP vs HTTPS

Bearer authentication controls **who may call the API**, but HTTP does not encrypt the token in transit.

For trusted LAN/VM use, restrict the Commander port with Windows Firewall. For untrusted networks, use HTTPS or a trusted tunnel/VPN such as WireGuard/Tailscale. The browser UI automatically hides the plaintext-token warning when loaded over HTTPS.

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

`desiredQty` means the final desired count. If `desiredQty = 3`, Commander submits at most one new order per confirmed success rather than requesting quantity 3 in one ambiguous transaction.

For unattended `--auto-pay`, the default policy requires a finite total budget. If the budget gate is disabled or unlimited, unattended automatic payment is blocked.

Any ambiguous checkout result enters REVIEW; the global auto-order arm is dropped until the operator verifies the provider-side order/payment state.

## Availability vs orderability

OVH/Kimsufi availability data can contain configurations that are not present in the current order/cart option set.

Commander classifies them as:

- **Orderable**: configuration can currently be represented in the cart/order option set.
- **Availability-only**: availability reports the configuration, but it cannot currently be constructed for ordering.
- **Unknown**: orderability metadata has not been resolved yet.

Availability-only rows may still be observed, but they are not eligible for auto-order.

## Pricing

Commander does not assume the Plan base price is the complete configuration price.

It uses OVH public cart pricing data to estimate:

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
  "allowStopBackend": true,
  "apiTokenEnv": "KSC_API_TOKEN"
}
```

Remote examples are included for both unauthenticated trusted-LAN use and authenticated remote use.

`192.0.2.10` is a documentation-only example address; replace it with your own trusted host.

## Notifications

Supported notification channels:

- Windows Tray balloon notifications;
- Telegram Bot API.

Telegram token is read from an environment variable (default `KSC_TELEGRAM_BOT_TOKEN`) rather than stored in settings JSON.

## Data directory

Runtime state is written under `data/` and intentionally ignored by Git. Do not commit a populated `data/` directory; it may contain operational metadata and order history.

## Tests

Run all Python regression tests from the repository root:

```powershell
Get-ChildItem .\tests\test_*.py | ForEach-Object { py -3 $_.FullName }
```

Optionally validate frontend JavaScript with Node.js:

```powershell
node --check .\ui\app.js
```

The suite covers pricing, option mapping, orderability, order safety, snapshot concurrency, pending UI state, replay/optimizer behavior, Tray/backend decoupling, API auth, browser auth and Tray auth.

## Legacy watcher

`legacy/watch-kimsufi-autoorder-P1Y.ps1` is retained for historical/reference purposes only.

In the public repository its auto-order flag is **disabled by default**. Prefer Stock Commander for live use.

## Third-party dependency

This project invokes `TheoBrigitte/kimsufi-notifier` as an external executable. It is not bundled here and is distributed by its upstream author under the MIT License. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

MIT. See [LICENSE](LICENSE).