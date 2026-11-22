# 从旧 PowerShell watcher 到 Stock Commander

旧脚本保存在 `legacy/watch-kimsufi-autoorder-P1Y.ps1`，用于对照，不由新程序自动执行。

## 旧逻辑 -> 新逻辑

- `$targets` 写死数组 -> GUI 中动态目录 + `data/targets.json`
- 单一 `$orderSuccessLock` -> 每个目标独立成功 receipt + `completedQty`
- 单一 `$orderReviewLock` -> 每个目标独立 REVIEW lock
- `$orderActiveGuard` -> 全局 checkout guard + 每目标 `.active.order`
- `quantity=1` -> 保留；GUI 的目标数量是最终数量，逐台下单
- `datacenters=any` -> GUI 机房策略先决策出具体 DC，再以 `--datacenter <dc>` 下单
- 固定 P0/P1 -> 任意精确 RAM/storage 变体可单独监控/自动下单

## 为什么不自动导入旧 P0

旧脚本中的 P0 是真实可自动支付的 KS-5 订单。为了避免第一次打开新 GUI 时误把历史 P0 一起武装，新程序默认不导入任何自动下单目标。需要的目标请在目录表中重新勾选。


## v0.3 -> v0.4

`settings.json` 自动合并新的 `notifications` 默认配置。升级后通知总开关默认启用，但 Telegram 默认关闭；只有运行 `KimsufiTray.ps1` 时才会显示桌面气泡。

新增：

- `data/notification-history.json`
- `KimsufiTray.ps1`
- `start-tray.cmd`
- `install-autostart.ps1`
- `uninstall-autostart.ps1`


## v0.4 -> v0.5

无需修改旧 `settings.json` 或 `targets.json`。

第一次启动 v0.5：

- 若已有 `data/events.csv`
- 且还没有 `data/stock-history.jsonl`

程序会把旧的 `AVAILABLE / DC_CHANGE / SOLD_OUT` 事件回填到新的历史文件。

旧事件没有历史 `pollIntervalSeconds`，因此统计页会保留该缺失信息，而不会用当前轮询周期冒充过去的周期。


## v0.5 -> v0.6

无需迁移数据文件。Watch Index 直接读取现有 `stock-history.jsonl`。

升级后：

- 不新增自动购买权限；
- 不修改现有 target 的 `orderPriority`；
- 不修改机房、付款周期或预算策略；
- 没有历史样本的目标只显示 `— / 样本不足`。

模型使用 UTC 星期/小时，并只作为雷达页和主服务器表的只读提示。


## v0.6 -> v0.7

不需要数据迁移。

v0.7 的策略回放直接读取现有 `data/stock-history.jsonl` 和当前 `dcStrategy`。不会修改历史记录，也不会写入自动下单策略。

旧于 v0.5 的历史因为缺少每个库存边界的 `pollIntervalSeconds`，可以参与名义回放，但保守/乐观边界的覆盖率会低于 100%。这是预期行为，不会自动猜测旧轮询周期。


v0.7 的组合采购回放也不新增持久化状态；它直接读取现有 targets、budgetPolicy 和 stock-history。修改回放输入不会写回自动采购配置。


## v0.7 -> v0.8

无需迁移数据格式。v0.8 只新增 `/api/optimizer` 与 GUI Pareto 优化区，不新增 live 配置字段，也不会在升级后自动运行优化或修改现有策略。


## v0.8 -> v0.9

新增 `data/price-cache.json`。旧 settings/targets 可直接继续使用。Endpoint 或 Country 变化时价格缓存自动清空。

升级后建议首次进入运行参数点击“扫描 OVH 付款组合”，让主表获得当前目录各 Plan 的结算价预览。


## v0.9 -> v0.10

新增：

```text
data/config-price-cache.json
```

旧的 `data/price-cache.json` 可继续保留。第一次运行 v0.10 后，请在目录加载完成后点击：

```text
扫描 OVH 付款组合 + 配置价格
```

以建立配置级 option 价格缓存。

v0.10 不改变已有 targets、自动下单武装状态、预算账本或库存历史。


## v0.10 -> v0.11

可以完整保留 v0.10 的 `data/`。

v0.11 新增 `ovh-pricing-cache.json`，旧的 `config-price-cache.json` 不再作为主价格源。第一次启动后请等待目录加载完成，再执行一次“扫描 OVH 付款组合 + 配置价格”。

无需删除：

- targets.json
- settings.json
- stock-history.jsonl
- budget-ledger.json
- notification-history.json
- receipts/

旧 price cache 可以保留；v0.11 的精确配置价格只有在新的 OVH 原始价格缓存存在时才会被标记为完整。


## v0.11 -> v0.12

无需删除既有 `data/`。升级后建议重新执行一次“扫描 OVH 付款组合 + 配置价格”，
让旧 SKU 使用新的 option matcher 重新建立价格映射。

若某个配置仍显示 `option_missing:*`，系统不会猜价或自动下单；可以继续从
`ovh-pricing-cache.json` 与内部 match diagnostics 定位该 SKU。


## v0.12 -> v0.13

保留现有 `data/`。升级后重新执行一次“扫描 OVH 付款组合 + 配置价格”，生成 `catalog-option-cache.json`。
基础默认配置将不再显示 `option_missing:*`；它们会显示 Plan + 配置 0.00，并在自动下单时省略对应 item-option。


## v0.13 -> v0.14

保留 `data/` 即可。旧 `catalog-option-cache.json` 条目会因为 schema 升级自动失效；启动后重新执行一次“扫描 OVH 付款组合 + 配置价格”。


## v0.14 -> v0.15

无需删除 `data/`。升级后重新执行一次“扫描 OVH 付款组合 + 配置价格”，程序会根据
availability 配置与当前 cart option 集合计算 `orderable / availability_only / unknown`。

已有目标若对应 availability-only 配置，监控状态仍可保留，但自动下单会被后端阻止。


## v0.15 -> v0.16

不需要迁移数据。直接保留现有 `data/`。

这是纯 Web UI 状态同步修复。升级后浏览器建议执行一次强制刷新
（Ctrl+F5），避免旧 `app.js` 被浏览器缓存。


## v0.16 -> v0.17

无需迁移数据，保留原有 `data/` 即可。

这是 Web state pipeline 与后端快照并发修复。升级后请：

1. 完全停止旧 v0.16 进程；
2. 覆盖程序文件但保留 `data/`；
3. 重新运行 `start-live.cmd`；
4. 浏览器执行一次 **Ctrl+F5**，确保加载 v0.17 `app.js`。

如果需要观察后端状态，可直接打开 `/api/health`，查看完整 state 快照最后构建耗时与当前是否正在构建。


## v0.17 -> v0.18

保留现有 `data/` 即可。

无人值守使用 `--auto-pay` 前必须在“预算策略”中：

1. 勾选“启用自动下单预算闸门”；
2. 设置 `自动购买总预算 > 0`；
3. 建议同时设置“单次预算占用上限”和足够的百分比/固定缓冲。

否则 v0.18 会以 `finite_budget_required` 阻止自动支付。

任何 checkout 进入 REVIEW 时，v0.18 会自动关闭“自动下单总开关”。检查 OVH 订单记录并确认没有已创建/已付款订单后，才应清除 REVIEW 并重新武装。

启动 v0.18 前请完全退出所有 v0.17 及更早版本；新的用户级全局 instance lock 只能约束 v0.18+ 实例。


## v0.18 -> v0.19

保留现有 `data/` 即可。新增 `KimsufiTray.json` 和 `KimsufiServer.json`。
本机一体模式保持默认配置即可。远程 Tray 将 `startBackend=false`、`allowStopBackend=false`，并把 `serverHost/serverPort` 指向 Commander 所在主机。
浏览器升级后建议 Ctrl+F5 一次。


## v0.19 -> v0.20

无需迁移 `data/`。鉴权默认关闭。远程部署建议生成 `KSC_API_TOKEN`，将 `KimsufiServer.json` 的 `authEnabled` 改为 true，然后重启 Commander；每台远程 Tray 设置同一环境变量并重启。浏览器首次访问会弹 Token 输入框。升级后建议 Ctrl+F5。


## v0.20.1 -> v0.20.2

无需迁移数据。此版本只整理浏览器认证 UI/状态机。升级后建议 Ctrl+F5 强制刷新前端资源。


## v0.20.2 -> v0.20.3

无需迁移数据。认证逻辑没有变化；仅调整浏览器认证弹窗布局与样式。升级后建议 Ctrl+F5。

## v0.20.3 -> v0.21.9

无需迁移 `data/`。建议升级后浏览器执行一次 **Ctrl+F5**。

v0.21.9 推荐使用双 Token：

- `KSC_READ_TOKEN`：长期 Read 客户端；
- `KSC_ADMIN_TOKEN`：管理员能力；
- `KSC_API_TOKEN`：旧单 Token 兼容回退，仍按 Admin 处理。

直接运行：

```powershell
powershell -File .\\generate-api-token.ps1
```

默认会生成并写入 Read + Admin 两枚用户环境变量。远程 Tray 默认使用 `KSC_READ_TOKEN`，管理员操作可临时输入 Admin Token，选择仅执行此次或提升当前 Tray 会话。

Windows PowerShell 5.1 用户应保留发行包中的原始 `.ps1` 文件编码；v0.21.9 的 PowerShell 脚本使用 UTF-8 with BOM。
