# Changelog

## v0.21.9

Capability-based Read/Admin access-control release.

Highlights:

- separate Read and Admin tokens with legacy single-token compatibility;
- capability-aware API authorization and explicit `403 admin_required` responses;
- Web step-up authorization: one-shot Admin action or session Admin elevation;
- privileged controls remain visible in Read mode and are visually marked;
- remote Tray defaults to Read and supports one-shot/session Admin elevation;
- session Admin can be explicitly dropped back to Read;
- token helper generates Read + Admin tokens by default and is compatible with Windows PowerShell 5.1;
- PowerShell scripts are UTF-8 with BOM and reject smart-quote regressions;
- WinForms Admin dialog uses a native Windows shield icon;
- Tray network traffic is asynchronous and no longer blocks the UI when Commander is slow/offline;
- timeout/network/401/403/5xx semantics are separated;
- Admin verification de-duplicates stale callbacks;
- per-Commander Tray single-instance mutex uses a safe hashed instance key.


## v0.20.3

Authentication and remote-management release.

Highlights:

- optional Web/API Bearer Token authentication;
- fail-closed startup when authentication is enabled but the configured token is unavailable/invalid;
- browser authentication with sessionStorage/localStorage modes;
- authenticated remote Tray requests via environment-provided token;
- secure remote examples and token generation helper;
- browser auth UX cleanup across v0.20.1–v0.20.3:
  - closable management dialog after successful authentication;
  - distinct unauthenticated/stale-token/authenticated states;
  - password-masked token input with show/hide control;
  - Enter-to-submit and disabled empty-token submit;
  - improved layout and remember-token control;
  - dynamic HTTP plaintext-token warning that automatically hides under HTTPS.

The public release keeps environment-neutral paths/addresses and keeps the legacy watcher auto-order switch disabled by default.

## v0.19.0

First public source release.

Highlights:

- visual Kimsufi catalog and exact-configuration monitoring;
- RAM, storage type, availability and price filtering;
- datacenter policy engine;
- payment duration / pricing-mode discovery;
- configuration-level monthly and billing-period price estimates;
- fail-closed auto-order flow with quantity tracking, REVIEW state, persistent safety markers and finite-budget protection;
- Windows tray integration, desktop/Telegram notifications and autostart helper;
- stock history, Watch Index, replay and Pareto strategy optimizer;
- availability/orderability separation for availability-only configurations;
- remote Tray/backend decoupling with independent host/port configuration;
- state snapshot and UI pending-state fixes for long-running unattended operation.

### Public-release hygiene changes

The public v0.19 source uses environment-neutral example paths/addresses and disables auto-order in the legacy PowerShell reference script by default. Core v0.19 behavior is otherwise retained.