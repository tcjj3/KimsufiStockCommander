# Changelog

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