# Kimsufi Stock Commander

Kimsufi Stock Commander 是一个面向 Windows 的 Kimsufi 可视化库存监控与保守自动下单控制台，支持精确配置、机房策略、付款周期/价格模式、预算闸门、通知、库存历史、回放与策略优化。

> **当前公开版本：v0.19.0**  
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

v0.19 **没有 Web/API 鉴权**。默认只监听 `127.0.0.1`。如果绑定 `0.0.0.0` 供 VM/局域网使用，请只在可信网络中使用，并通过 Windows Firewall 限制可访问 8765 的管理机 IP；不要直接暴露到公网。

## 主要能力

- 动态读取 Kimsufi 目录；
- 按精确 RAM/storage 配置监控库存；
- RAM、NVMe/SATA SSD/HDD、库存状态和价格区间筛选；
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
- 库存窗口历史、Watch Index、置信度；
- 轮询/DC 策略历史回放；
- 多目标 priority/budget 回放；
- Pareto 自动策略优化器；
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

## 凭证

示例仅说明变量名：

```powershell
$env:OVH_APP_KEY="..."
$env:OVH_APP_SECRET="..."
$env:OVH_CONSUMER_KEY="..."
py -3 .\KimsufiStockCommander.py --open-browser
```

不要把真实凭证写进源码或 JSON。

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

## Availability ≠ Orderability

实际测试发现，availability 数据可能出现当前 cart/order options 根本无法构造的配置。

v0.19 将其分为：

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
  "allowStopBackend": true
}
```

远程模式参考 `KimsufiTray.remote-example.json`，通常设置：

```json
{
  "serverHost": "192.0.2.10",
  "serverPort": 8765,
  "startBackend": false,
  "allowStopBackend": false
}
```

`192.0.2.10` 只是文档示例地址，请替换成自己的可信宿主机地址。

### v0.19 远程安全边界

v0.19 没有 Web/API 登录鉴权。Commander 如果监听 `0.0.0.0`，必须使用 Windows Firewall 把端口限制给指定 VM/管理机。不要将 v0.19 直接暴露在不可信网络或公网。

## 通知

支持：

- Windows Tray 气泡通知；
- Telegram Bot API。

Telegram Token 默认从 `KSC_TELEGRAM_BOT_TOKEN` 环境变量读取，不写入 settings JSON。

## data 目录

运行态数据写入 `data/`，仓库已默认忽略：

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

不要把已有运行态 `data/` 提交到公开仓库。

## 测试

运行全部 Python 回归测试：

```powershell
Get-ChildItem .\tests\test_*.py | ForEach-Object { py -3 $_.FullName }
```

如果安装了 Node.js，还可以检查前端 JS：

```powershell
node --check .\ui\app.js
```

## Legacy watcher

`legacy/watch-kimsufi-autoorder-P1Y.ps1` 仅保留用于历史/实现对照。公开仓库中它的自动下单开关默认关闭。实时使用优先使用 Stock Commander。

## 第三方依赖

本项目通过外部进程调用 `TheoBrigitte/kimsufi-notifier`，不捆绑其二进制或源码。上游项目采用 MIT License，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## License

MIT，见 [LICENSE](LICENSE)。