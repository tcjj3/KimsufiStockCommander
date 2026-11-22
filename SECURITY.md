# Security policy

## Important deployment boundary for v0.19

Kimsufi Stock Commander v0.19 does **not** provide Web/API authentication.

The default server bind address is `127.0.0.1`. Keep that default unless you understand the consequences.

If you bind to `0.0.0.0` or another LAN address:

- expose it only on a trusted private network;
- restrict the listening port with Windows Firewall to specific management hosts/VMs;
- do not expose the Web/API port directly to the public Internet;
- remember that anyone able to reach the API can potentially change monitoring and auto-order settings.

A later authenticated build is intentionally not included in the v0.19 public release until it has been separately tested.

## Credentials

The application reads OVH credentials only from process environment variables:

- `OVH_APP_KEY`
- `OVH_APP_SECRET`
- `OVH_CONSUMER_KEY`

Telegram bot tokens are also read from an environment variable (default: `KSC_TELEGRAM_BOT_TOKEN`).

Do not commit credentials, populated `data/` directories, diagnostic bundles, logs or screenshots containing secrets.

## Auto-ordering risk

Auto-ordering can create real paid orders. Use DEMO mode first. Keep the global auto-order switch disarmed until targets, quantity, datacenter policy, payment duration/mode and finite budget limits have been reviewed.

Ambiguous checkout results intentionally enter REVIEW rather than retrying automatically.

## Reporting a vulnerability

Please open a GitHub issue for non-sensitive defects. For vulnerabilities involving credentials or a practical unintended-purchase path, avoid posting live secrets, order IDs or API credentials in public issue content.

## Web/API authentication (v0.20+)

When remote access is required, enable Bearer Token authentication in `KimsufiServer.json` and provide the token via the configured environment variable (default `KSC_API_TOKEN`). Do not store the token in source-controlled JSON.

Authentication does not encrypt HTTP traffic. If the browser/Tray reaches Commander over HTTP, the Bearer Token is visible to an on-path observer. Use HTTPS or a trusted encrypted tunnel/VPN across untrusted networks. The browser UI warns on non-loopback HTTP and hides that warning automatically under HTTPS.

If `authEnabled=true` but the token is missing or too short, Commander intentionally refuses to start.