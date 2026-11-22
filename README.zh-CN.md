# Kimsufi Stock Commander

Kimsufi Stock Commander 是一个面向 Windows 的 Kimsufi 可视化库存监控与保守自动下单控制台，支持精确配置、机房策略、付款周期/价格模式、预算闸门、通知、库存历史、回放、策略优化，以及可选的 Web/API Bearer Token 鉴权。

> **当前公开版本：v0.20.3**  
> **许可证：MIT**  
> 本项目为独立社区工具，与 OVHcloud/Kimsufi 无隶属或官方支持关系。

## ⚠️ 自动下单前必读

自动下单会产生**真实付费订单**。

建议首次使用严格按以下顺序：

1. 先运行 DEMO；
2. 核对 Plan、RAM/storage、可下单性与价格；
3. 启用有限总预算；
4. 首次 live 目标建议 `desiredQty = 1`；
5. 最后才打开“自动下单总开关”。

项目采用保守失败（fail-closed）思路，但最终仍应由使用者确认目标配置、数量、机房策略、付款组合与预算。

## 主要能力

- 动态读取 Kimsufi 目录；
- 按精确 RAM/storage 配置监控库存；
- RAM、NVMe/SATA SSD/HDD、库存、可下单性和价格区间筛选；
- 任意/固定/白名单/优先级+回退/排除等机房策略；
- 动态发现 `price-duration / price-mode`；
- 按 OVH public cart 原始价格结构计算配置级月价与付款周期估算；
- 区分 **availability** 与 **orderability**，避免 availability-only“幽灵配置”进入自动下单；
- 保守自动采购：
  - 每次只下 1 台；
  - `desiredQty` 表示最终目标数量；
  - 只有明确出现 `order completed:` 才算成功；
  - 不明确结果进入持久 REVIEW，不自动重试；
  - active marker、receipt、进程/订单锁；
  - AutoPay 默认要求有限总预算；
- Windows Tray、登录自启、桌面/Telegram 通知；
- Tray 与 Commander 后端解耦，可独立配置连接 Host/Port、是否启动/停止后端；
- 库存历史、Watch Index、历史回放和 Pareto 策略优化；
- **v0.20+ 支持可选 Web/API Bearer Token 鉴权**；
- Python 后端只使用标准库，无 pip 依赖。

## 运行要求

- Windows 10/11 或 Windows Server；
- Python 3.10+；
- 单独安装 [`TheoBrigitte/kimsufi-notifier`](https://github.com/TheoBrigitte/kimsufi-notifier)；
- 真正下单时，通过进程环境变量提供：
  - `OVH_APP_KEY`
  - `OVH_APP_SECRET`
  - `OVH_CONSUMER_KEY`

默认 CLI 路径：

```text
%USERPROFILE%\go\bin\kimsufi-notifier.exe
```

也可以在界面中修改。

## 快速开始

### DEMO

```text
start-demo.cmd
```

或：

```powershell
py -3 .\KimsufiStockCommander.py --demo --open-browser
```

DEMO 模式强制禁止真实 checkout。

### Live

```text
start-live.cmd
```

默认页面：

```text
http://127.0.0.1:8765/
```

首次启动时自动下单总开关为关闭状态。

## OVH 凭证

示例仅说明变量名：

```powershell
$env:OVH_APP_KEY="..."
$env:OVH_APP_SECRET="..."
$env:OVH_CONSUMER_KEY="..."
py -3 .\KimsufiStockCommander.py --open-browser
```

不要把真实凭证写进源码或 JSON。

## Web/API 鉴权（v0.20+）

本机模式下鉴权默认关闭。

远程浏览器/Tray 使用时，先生成强随机 Token：

```powershell
.\generate-api-token.ps1 -SetUserEnvironment
```

然后在 `KimsufiServer.json` 开启鉴权：

```json
{
  "bindHost": "0.0.0.0",
  "port": 8765,
  "openBrowser": true,
  "authEnabled": true,
  "authTokenEnv": "KSC_API_TOKEN"
}
```

设置环境变量后需要重启 Commander。

如果 `authEnabled=true`，但指定环境变量不存在或 Token 太短，Commander 会直接拒绝启动，不会静默退回无鉴权模式。

### 浏览器认证

浏览器在服务器要求鉴权时会弹出 Bearer Token 输入框：

- 默认只保存在当前会话；
- 勾选“在此浏览器记住 Token”后才写入 `localStorage`；
- 已认证状态可更换 Token，或注销并清除本地 Token；
- 弹窗区分“本地无 Token / 已保存 Token 失效 / 已认证”三种状态；
- Token 输入框默认密码掩码，可手动显示/隐藏；
- HTTP 明文警告为动态判断，页面通过 HTTPS 打开时会自动隐藏。

### Tray 认证

Tray 从环境变量读取同一 Token：

```json
{
  "apiTokenEnv": "KSC_API_TOKEN"
}
```

远程 Tray 通常配置为：

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

可参考：

- `KimsufiTray.remote-auth-example.json`
- `KimsufiServer.secure-remote-example.json`

### HTTP / HTTPS 边界

Bearer Token 解决的是“谁能调用 API”，但 HTTP 本身不加密 Token。

可信 LAN/VM 场景建议配合 Windows Firewall 限制 Commander 端口；跨不可信网络则使用 HTTPS、WireGuard、Tailscale 等可信加密通道。当前页面如果是通过 HTTPS 打开，会自动隐藏 HTTP 明文 Token 警告。

## 自动下单安全模型

核心状态机：

```text
AVAILABLE
  ↓
可下单性 / DC / 付款组合 / 配置价 / 有限预算检查
  ↓
持久 active marker
  ↓
quantity=1 checkout
  ↓
明确出现 order completed:
  ├─ 成功 → receipt → completedQty + 1
  └─ 不明确 → REVIEW → 禁止自动重试
```

如果 `desiredQty = 3`，Commander 也是“确认一台成功后再考虑下一台”，而不是一次直接请求 3 台。

无人值守 AutoPay 默认要求启用有限总预算；预算未启用或总预算无限时会保守阻止自动付款。

任何不明确 checkout 结果都会进入 REVIEW，并解除全局自动下单武装，直到使用者核对服务商侧订单/付款状态。

## Availability ≠ Orderability

availability 数据可能出现当前 cart/order options 无法构造的配置。

Commander 将其分为：

- **可下单**：当前 cart 可构造；
- **Availability-only**：库存遥测存在，但当前无法下单；
- **待确认**：尚未完成可下单元数据解析。

Availability-only 可以继续观察，但不会自动采购。

## 配置价格

Commander 不把 Plan 基础价当作所有 RAM/storage 配置的真实价格。

当前使用 OVH public cart 价格数据分别估算：

- `P1M/default` 配置月价；
- 当前选择付款周期的配置价格；
- 可解析的 RAM/storage/其他 mandatory option 加价。

界面价格默认理解为未税估算。若 setup fee/其他一次性费用没有被数据源暴露，应使用固定/百分比预算缓冲覆盖。

## Tray 本地/远程模式

Tray 由 `KimsufiTray.json` 独立配置。

本机模式：

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

远程模式示例使用文档地址 `192.0.2.10`，请替换成自己的可信宿主机地址。

## 通知

支持：

- Windows Tray 气泡通知；
- Telegram Bot API。

Telegram Token 默认从 `KSC_TELEGRAM_BOT_TOKEN` 环境变量读取，不写入 settings JSON。

## data 目录

运行态数据写入 `data/`，仓库已默认忽略。不要把已有运行态 `data/` 提交到公开仓库。

## 测试

运行全部 Python 回归测试：

```powershell
Get-ChildItem .\tests\test_*.py | ForEach-Object { py -3 $_.FullName }
```

如果安装了 Node.js，还可以检查前端 JS：

```powershell
node --check .\ui\app.js
```

测试覆盖价格映射、可下单性、订单安全、snapshot 并发、UI pending、回放/优化、Tray/后端解耦、API 鉴权、浏览器鉴权和 Tray 鉴权。

## Legacy watcher

`legacy/watch-kimsufi-autoorder-P1Y.ps1` 仅保留用于历史/实现对照。公开仓库中它的自动下单开关默认关闭。实时使用优先使用 Stock Commander。

## 第三方依赖

本项目通过外部进程调用 `TheoBrigitte/kimsufi-notifier`，不捆绑其二进制或源码。上游项目采用 MIT License，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## License

MIT，见 [LICENSE](LICENSE)。