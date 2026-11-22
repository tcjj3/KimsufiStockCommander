param(
    [string]$ConfigPath = "",
    [string]$ServerHost = "",
    [int]$ServerPort = 0
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ConfigPath) { $ConfigPath = Join-Path $Root "KimsufiTray.json" }

$cfg = [ordered]@{
    serverScheme = "http"
    serverHost = "127.0.0.1"
    serverPort = 8765
    startBackend = $true
    backendBindHost = "127.0.0.1"
    backendPort = 8765
    allowStopBackend = $true
    requestTimeoutSeconds = 3
    notificationPollMs = 3000
}
if (Test-Path -LiteralPath $ConfigPath) {
    try {
        $raw = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
        foreach ($p in $raw.PSObject.Properties) {
            if ($cfg.Contains($p.Name)) { $cfg[$p.Name] = $p.Value }
        }
    } catch {
        [System.Windows.Forms.MessageBox]::Show("无法读取 Tray 配置：$($_.Exception.Message)","Kimsufi Tray") | Out-Null
    }
}
if ($ServerHost) { $cfg.serverHost = $ServerHost }
if ($ServerPort -gt 0) { $cfg.serverPort = $ServerPort }

function Format-UrlHost([string]$Value) {
    if ($Value -match ':' -and $Value -notmatch '^\[') { return "[$Value]" }
    return $Value
}
$UrlHost = Format-UrlHost ([string]$cfg.serverHost)
$BaseUrl = "{0}://{1}:{2}" -f [string]$cfg.serverScheme,$UrlHost,[int]$cfg.serverPort
$Backend = Join-Path $Root "KimsufiStockCommander.py"
$script:StartedBackend = $null
$script:SeenIds = New-Object 'System.Collections.Generic.HashSet[string]'
$script:Connected = $false

function Test-Commander {
    try {
        $r = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec ([int]$cfg.requestTimeoutSeconds)
        return [bool]$r.ok
    } catch { return $false }
}

function Start-CommanderBackend {
    if (-not [bool]$cfg.startBackend) { return }
    if (Test-Commander) { return }
    if (-not (Test-Path -LiteralPath $Backend)) { throw "Backend not found: $Backend" }
    $backendArg = '"' + $Backend + '"'
    $backendPort = [int]$cfg.backendPort
    $bindHost = [string]$cfg.backendBindHost
    $launchArgs = @('-3',$backendArg,'--host',$bindHost,'--port',"$backendPort")
    $pyw = Get-Command pyw.exe -ErrorAction SilentlyContinue
    if ($pyw) {
        $script:StartedBackend = Start-Process -FilePath $pyw.Source -ArgumentList $launchArgs -WorkingDirectory $Root -PassThru
    } else {
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $py) { throw "Python launcher py.exe/pyw.exe not found." }
        $script:StartedBackend = Start-Process -FilePath $py.Source -ArgumentList $launchArgs -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    }
}

function Invoke-CommanderPost([string]$Path) {
    try {
        Invoke-RestMethod -Method Post -Uri "$BaseUrl$Path" -ContentType 'application/json' -Body '{}' -TimeoutSec ([int]$cfg.requestTimeoutSeconds) | Out-Null
    } catch {}
}

try { Start-CommanderBackend } catch {
    [System.Windows.Forms.MessageBox]::Show("启动本地 Commander 失败：$($_.Exception.Message)`nTray 仍会继续运行并尝试连接 $BaseUrl", "Kimsufi Tray") | Out-Null
}

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.Text = "Kimsufi Stock Commander"
$notify.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$serverItem = $menu.Items.Add("服务器：$BaseUrl")
$serverItem.Enabled = $false
$statusItem = $menu.Items.Add("状态：连接中…")
$statusItem.Enabled = $false
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
$openItem = $menu.Items.Add("打开控制台")
$pollItem = $menu.Items.Add("立即轮询")
$catalogItem = $menu.Items.Add("刷新目录")
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
$exitTrayItem = $menu.Items.Add("退出托盘")
$stopItem = $null
if ([bool]$cfg.allowStopBackend -and [bool]$cfg.startBackend) {
    $stopItem = $menu.Items.Add("停止由托盘启动的 Commander 并退出")
}
$notify.ContextMenuStrip = $menu

$openAction = { Start-Process "$BaseUrl/" }
$openItem.add_Click($openAction)
$notify.add_DoubleClick($openAction)
$pollItem.add_Click({ Invoke-CommanderPost '/api/poll' })
$catalogItem.add_Click({ Invoke-CommanderPost '/api/catalog/refresh' })

function Update-ConnectionState([bool]$Connected) {
    if ($script:Connected -eq $Connected) { return }
    $script:Connected = $Connected
    $statusItem.Text = if ($Connected) { "状态：已连接" } else { "状态：未连接（自动重试）" }
    $pollItem.Enabled = $Connected
    $catalogItem.Enabled = $Connected
    $notify.Text = if ($Connected) { "Kimsufi Stock Commander · connected" } else { "Kimsufi Stock Commander · disconnected" }
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = [Math]::Max(1000,[int]$cfg.notificationPollMs)
$timer.add_Tick({
    try {
        $state = Invoke-RestMethod -Uri "$BaseUrl/api/state" -TimeoutSec ([int]$cfg.requestTimeoutSeconds)
        Update-ConnectionState $true
        $desktop = $true
        if ($null -ne $state.settings.notifications.desktop) { $desktop = [bool]$state.settings.notifications.desktop }
        foreach ($x in (@($state.notifications) | Select-Object -First 50 | Sort-Object time)) {
            if ([string]::IsNullOrWhiteSpace([string]$x.id)) { continue }
            if ($script:SeenIds.Add([string]$x.id) -and $desktop) {
                $icon = [System.Windows.Forms.ToolTipIcon]::Info
                if ($x.severity -eq 'error') { $icon = [System.Windows.Forms.ToolTipIcon]::Error }
                elseif ($x.severity -eq 'warning') { $icon = [System.Windows.Forms.ToolTipIcon]::Warning }
                $notify.ShowBalloonTip(8000,[string]$x.title,[string]$x.message,$icon)
            }
        }
    } catch { Update-ConnectionState $false }
})

# Prime history if reachable; otherwise remain resident and reconnect later.
try {
    $initial = Invoke-RestMethod -Uri "$BaseUrl/api/state" -TimeoutSec ([int]$cfg.requestTimeoutSeconds)
    foreach ($x in @($initial.notifications)) { if ($x.id) { [void]$script:SeenIds.Add([string]$x.id) } }
    Update-ConnectionState $true
} catch { Update-ConnectionState $false }
$timer.Start()

$exitTrayItem.add_Click({
    $timer.Stop(); $notify.Visible=$false; $notify.Dispose(); [System.Windows.Forms.Application]::Exit()
})
if ($stopItem) {
    $stopItem.add_Click({
        $timer.Stop()
        if ($script:StartedBackend -and -not $script:StartedBackend.HasExited) {
            try { Stop-Process -Id $script:StartedBackend.Id -Force } catch {}
        }
        $notify.Visible=$false; $notify.Dispose(); [System.Windows.Forms.Application]::Exit()
    })
}

[System.Windows.Forms.Application]::Run()