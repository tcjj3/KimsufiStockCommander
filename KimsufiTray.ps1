param(
    [string]$ConfigPath = "",
    [string]$ServerHost = "",
    [int]$ServerPort = 0
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Net.Http

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
    apiTokenEnv = "KSC_READ_TOKEN"
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
$mutexSeed = "{0}|{1}|{2}" -f [string]$cfg.serverScheme,[string]$cfg.serverHost,[int]$cfg.serverPort
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $mutexHashBytes = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($mutexSeed))
} finally {
    $sha256.Dispose()
}
$mutexKey = ([System.BitConverter]::ToString($mutexHashBytes)).Replace('-', '').Substring(0, 32)
$mutexName = "Local\KimsufiStockCommanderTray_$mutexKey"
$script:InstanceMutex = New-Object -TypeName System.Threading.Mutex -ArgumentList @($false, $mutexName)
$script:OwnsInstanceMutex = $false
try {
    $script:OwnsInstanceMutex = $script:InstanceMutex.WaitOne(0, $false)
} catch [System.Threading.AbandonedMutexException] {
    $script:OwnsInstanceMutex = $true
}
if (-not $script:OwnsInstanceMutex) {
    [System.Windows.Forms.MessageBox]::Show("该 Commander 地址已经有一个 Kimsufi Tray 实例正在运行：`r`n$BaseUrl","Kimsufi Tray",[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    try { $script:InstanceMutex.Dispose() } catch {}
    exit 0
}

function Release-TrayInstanceMutex {
    if ($script:OwnsInstanceMutex -and $null -ne $script:InstanceMutex) {
        try { $script:InstanceMutex.ReleaseMutex() } catch {}
        $script:OwnsInstanceMutex = $false
    }
    if ($null -ne $script:InstanceMutex) {
        try { $script:InstanceMutex.Dispose() } catch {}
        $script:InstanceMutex = $null
    }
}
$Backend = Join-Path $Root "KimsufiStockCommander.py"
$script:StartedBackend = $null
$script:SeenIds = New-Object 'System.Collections.Generic.HashSet[string]'
$script:Connected = $false
$script:ConnectionDetail = "连接中…"
$script:AuthRole = ""
$script:AdminAvailable = $false
$script:SessionAdminToken = ""
$script:PendingHttp = New-Object System.Collections.ArrayList
$script:StatePollInFlight = $false
$script:ProbeInFlight = $false
$script:AdminActionInFlight = $false
$script:AdminVerifyGeneration = 0
$script:InitialHistoryPrimed = $false
$script:HttpClient = New-Object System.Net.Http.HttpClient
$script:HttpClient.Timeout = [TimeSpan]::FromSeconds([Math]::Max(1, [int]$cfg.requestTimeoutSeconds))

function Get-ConfiguredApiToken {
    $name = [string]$cfg.apiTokenEnv
    if ([string]::IsNullOrWhiteSpace($name)) { return "" }
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    if ($null -eq $value) { return "" }
    return [string]$value
}

function Get-EffectiveApiToken {
    if (-not [string]::IsNullOrWhiteSpace([string]$script:SessionAdminToken)) {
        return [string]$script:SessionAdminToken
    }
    return Get-ConfiguredApiToken
}

function Get-AuthHeaders([string]$Token = "") {
    $tokenValue = $Token
    if ([string]::IsNullOrWhiteSpace($tokenValue)) { $tokenValue = Get-EffectiveApiToken }
    if ([string]::IsNullOrWhiteSpace($tokenValue)) { return @{} }
    return @{ Authorization = "Bearer $tokenValue" }
}

function Invoke-CommanderGetSync([string]$Path, [string]$Token = "") {
    return Invoke-RestMethod -Uri "$BaseUrl$Path" -Headers (Get-AuthHeaders $Token) -TimeoutSec ([int]$cfg.requestTimeoutSeconds)
}

function Test-CommanderSync {
    try {
        $health = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec ([int]$cfg.requestTimeoutSeconds)
        if (-not [bool]$health.ok) {
            $script:ConnectionDetail = "健康检查失败"
            return $false
        }
        $authProp = $health.PSObject.Properties['authRequired']
        $authRequired = ($null -ne $authProp -and $authProp.Value -eq $true)
        if ($authRequired) {
            $token = Get-EffectiveApiToken
            if ([string]::IsNullOrWhiteSpace($token)) {
                $script:ConnectionDetail = "未认证：环境变量 $([string]$cfg.apiTokenEnv) 未设置"
                $script:AuthRole = ""
                $script:AdminAvailable = $false
                return $false
            }

            $check = $null
            try {
                $check = Invoke-CommanderGetSync '/api/auth/check' $token
            } catch {
                # A temporary Admin token may become invalid after the server
                # rotates credentials. Fall back to the configured Read token
                # instead of disconnecting the Tray completely.
                if (-not [string]::IsNullOrWhiteSpace([string]$script:SessionAdminToken)) {
                    $script:SessionAdminToken = ""
                    $fallback = Get-ConfiguredApiToken
                    if (-not [string]::IsNullOrWhiteSpace($fallback)) {
                        try { $check = Invoke-CommanderGetSync '/api/auth/check' $fallback } catch { $check = $null }
                    }
                }
                if ($null -eq $check) {
                    $script:ConnectionDetail = "未认证：Token 无效或已变更"
                    $script:AuthRole = ""
                    $script:AdminAvailable = $false
                    return $false
                }
            }

            if (-not [bool]$check.authenticated) {
                $script:ConnectionDetail = "未认证：Token 被拒绝"
                $script:AuthRole = ""
                $script:AdminAvailable = $false
                return $false
            }

            $roleProp = $check.PSObject.Properties['role']
            $script:AuthRole = if ($null -ne $roleProp -and -not [string]::IsNullOrWhiteSpace([string]$roleProp.Value)) { [string]$roleProp.Value } else { "admin" }
            $adminProp = $check.PSObject.Properties['adminAvailable']
            $script:AdminAvailable = ($null -eq $adminProp -or [bool]$adminProp.Value)

            if ($script:AuthRole -eq "admin") {
                if (-not [string]::IsNullOrWhiteSpace([string]$script:SessionAdminToken)) {
                    $script:ConnectionDetail = "已连接 · Admin（本次 Tray 会话）"
                } else {
                    $script:ConnectionDetail = "已连接 · Admin"
                }
            } else {
                $script:ConnectionDetail = "已连接 · Read"
            }
            return $true
        }

        $script:AuthRole = "admin"
        $script:AdminAvailable = $true
        $script:ConnectionDetail = "已连接 · API 未启用鉴权"
        return $true
    } catch {
        $script:AuthRole = ""
        $script:AdminAvailable = $false
        $script:ConnectionDetail = "未连接（自动重试）"
        return $false
    }
}

function Start-CommanderBackend {
    if (-not [bool]$cfg.startBackend) { return }
    if (Test-CommanderSync) { return }
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

function Start-HttpRequest([string]$Method, [string]$Path, [string]$Token, [string]$Kind, $Context = $null) {
    $request = New-Object System.Net.Http.HttpRequestMessage
    $request.Method = if ($Method -eq 'POST') { [System.Net.Http.HttpMethod]::Post } else { [System.Net.Http.HttpMethod]::Get }
    $request.RequestUri = New-Object System.Uri("$BaseUrl$Path")
    if (-not [string]::IsNullOrWhiteSpace($Token)) {
        $request.Headers.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue -ArgumentList @('Bearer', $Token)
    }
    if ($Method -eq 'POST') {
        $request.Content = New-Object System.Net.Http.StringContent -ArgumentList @('{}', [System.Text.Encoding]::UTF8, 'application/json')
    }

    try {
        $responseTask = $script:HttpClient.SendAsync($request)
        $op = [pscustomobject]@{
            Request = $request
            ResponseTask = $responseTask
            Response = $null
            ContentTask = $null
            Kind = $Kind
            Context = $Context
        }
        [void]$script:PendingHttp.Add($op)
        return $true
    } catch {
        try { $request.Dispose() } catch {}
        return $false
    }
}

function Start-CommanderGetAsync([string]$Path, [string]$Token, [string]$Kind, $Context = $null) {
    return Start-HttpRequest 'GET' $Path $Token $Kind $Context
}

function Start-CommanderPostAsync([string]$Path, [string]$Token, [string]$Kind, $Context = $null) {
    return Start-HttpRequest 'POST' $Path $Token $Kind $Context
}

function Parse-JsonBody($Result) {
    if ($null -eq $Result -or -not [bool]$Result.Ok -or [string]::IsNullOrWhiteSpace([string]$Result.Body)) { return $null }
    try { return ([string]$Result.Body | ConvertFrom-Json) } catch { return $null }
}

function New-HttpFailureResult($Exception) {
    $base = $Exception
    try { $base = $Exception.GetBaseException() } catch {}

    $kind = "network"
    if ($base -is [System.Threading.Tasks.TaskCanceledException] -or $base -is [System.OperationCanceledException]) {
        $kind = "timeout"
    } elseif ($base -is [System.Net.Http.HttpRequestException]) {
        $kind = "network"
    }

    $detail = ""
    try { $detail = [string]$base.Message } catch {}
    return [pscustomobject]@{
        Ok = $false
        StatusCode = 0
        Body = ''
        Error = $detail
        FailureKind = $kind
    }
}

function Get-HttpFailureText($Result, [string]$Subject = "请求") {
    $kind = ""
    if ($null -ne $Result -and $null -ne $Result.PSObject.Properties['FailureKind']) {
        $kind = [string]$Result.FailureKind
    }
    $status = 0
    try { $status = [int]$Result.StatusCode } catch {}

    if ($kind -eq "timeout") { return "$Subject 超时，Commander 未返回响应。" }
    if ($kind -eq "network") {
        $detail = [string]$Result.Error
        if ([string]::IsNullOrWhiteSpace($detail)) { return "$Subject 失败：无法连接 Commander。" }
        return "$Subject 失败：无法连接 Commander。$detail"
    }
    if ($status -ge 500) { return "$Subject 失败：Commander 返回 HTTP $status。" }
    if ($status -gt 0) { return "$Subject 失败：HTTP $status。" }
    if (-not [string]::IsNullOrWhiteSpace([string]$Result.Error)) { return "$Subject 失败：$([string]$Result.Error)" }
    return "$Subject 失败。"
}

function Set-Disconnected([string]$Detail = "未连接（自动重试）") {
    $script:AuthRole = ""
    $script:AdminAvailable = $false
    $script:ConnectionDetail = $Detail
    Update-ConnectionState $false
}

function Apply-AuthCheck($Check) {
    if ($null -eq $Check -or -not [bool]$Check.authenticated) {
        Set-Disconnected "未认证：Token 被拒绝"
        return $false
    }

    $roleProp = $Check.PSObject.Properties['role']
    $script:AuthRole = if ($null -ne $roleProp -and -not [string]::IsNullOrWhiteSpace([string]$roleProp.Value)) { [string]$roleProp.Value } else { "admin" }
    $adminProp = $Check.PSObject.Properties['adminAvailable']
    $script:AdminAvailable = ($null -eq $adminProp -or [bool]$adminProp.Value)

    if ($script:AuthRole -eq "admin") {
        if (-not [string]::IsNullOrWhiteSpace([string]$script:SessionAdminToken)) {
            $script:ConnectionDetail = "已连接 · Admin（本次 Tray 会话）"
        } else {
            $script:ConnectionDetail = "已连接 · Admin"
        }
    } else {
        $script:ConnectionDetail = "已连接 · Read"
    }
    Update-ConnectionState $true
    return $true
}

function Start-ConnectionProbe([bool]$AllowSessionFallback = $true) {
    if ($script:ProbeInFlight) { return }
    $script:ProbeInFlight = $true
    $ctx = [pscustomobject]@{ AllowSessionFallback = $AllowSessionFallback }
    if (-not (Start-CommanderGetAsync '/api/health' '' 'probe-health' $ctx)) {
        $script:ProbeInFlight = $false
        Set-Disconnected "未连接（自动重试）"
    }
}

function Handle-StateResult($Result) {
    $script:StatePollInFlight = $false
    if (-not [bool]$Result.Ok) {
        if (($Result.StatusCode -eq 401 -or $Result.StatusCode -eq 403) -and -not [string]::IsNullOrWhiteSpace([string]$script:SessionAdminToken)) {
            $script:SessionAdminToken = ""
        }
        $kind = if ($null -ne $Result.PSObject.Properties['FailureKind']) { [string]$Result.FailureKind } else { "" }
        if ($kind -eq "timeout") {
            Set-Disconnected "请求超时（自动重试）"
        } elseif ($kind -eq "network") {
            Set-Disconnected "无法连接（自动重试）"
        } elseif ([int]$Result.StatusCode -ge 500) {
            Set-Disconnected "Commander 服务端错误（自动重试）"
        } else {
            Set-Disconnected "未连接（自动重试）"
        }
        return
    }

    $state = Parse-JsonBody $Result
    if ($null -eq $state) {
        Set-Disconnected "状态响应解析失败"
        return
    }

    $desktop = $true
    try {
        if ($null -ne $state.settings.notifications.desktop) { $desktop = [bool]$state.settings.notifications.desktop }
    } catch {}

    $rows = @($state.notifications)
    if (-not $script:InitialHistoryPrimed) {
        foreach ($x in $rows) {
            if ($x.id) { [void]$script:SeenIds.Add([string]$x.id) }
        }
        $script:InitialHistoryPrimed = $true
        return
    }

    foreach ($x in ($rows | Select-Object -First 50 | Sort-Object time)) {
        if ([string]::IsNullOrWhiteSpace([string]$x.id)) { continue }
        if ($script:SeenIds.Add([string]$x.id) -and $desktop) {
            $icon = [System.Windows.Forms.ToolTipIcon]::Info
            if ($x.severity -eq 'error') { $icon = [System.Windows.Forms.ToolTipIcon]::Error }
            elseif ($x.severity -eq 'warning') { $icon = [System.Windows.Forms.ToolTipIcon]::Warning }
            $notify.ShowBalloonTip(8000,[string]$x.title,[string]$x.message,$icon)
        }
    }
}

function Start-StatePoll {
    if ($script:StatePollInFlight) { return }
    if (-not $script:Connected) {
        Start-ConnectionProbe
        return
    }
    $script:StatePollInFlight = $true
    if (-not (Start-CommanderGetAsync '/api/state' (Get-EffectiveApiToken) 'state' $null)) {
        $script:StatePollInFlight = $false
        Set-Disconnected "未连接（自动重试）"
    }
}

function Start-AdminPost([string]$ActionLabel, [string]$Path, [string]$Token) {
    if ($script:AdminActionInFlight) {
        $notify.ShowBalloonTip(2500,"Kimsufi Stock Commander","已有管理员操作正在处理。",[System.Windows.Forms.ToolTipIcon]::Info)
        return
    }
    $script:AdminActionInFlight = $true
    $notify.ShowBalloonTip(2200,"Kimsufi Stock Commander","$ActionLabel 正在提交…",[System.Windows.Forms.ToolTipIcon]::Info)
    $ctx = [pscustomobject]@{ ActionLabel = $ActionLabel; Path = $Path }
    if (-not (Start-CommanderPostAsync $Path $Token 'admin-post' $ctx)) {
        $script:AdminActionInFlight = $false
        [System.Windows.Forms.MessageBox]::Show("$ActionLabel 失败：无法创建网络请求。","Kimsufi Tray",[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
    }
}

function Dispatch-HttpCompletion($Op, $Result) {
    switch ([string]$Op.Kind) {
        'probe-health' {
            if (-not [bool]$Result.Ok) {
                $script:ProbeInFlight = $false
                $kind = if ($null -ne $Result.PSObject.Properties['FailureKind']) { [string]$Result.FailureKind } else { "" }
                if ($kind -eq "timeout") {
                    Set-Disconnected "请求超时（自动重试）"
                } elseif ($kind -eq "network") {
                    Set-Disconnected "无法连接（自动重试）"
                } elseif ([int]$Result.StatusCode -ge 500) {
                    Set-Disconnected "Commander 服务端错误（自动重试）"
                } else {
                    Set-Disconnected "未连接（自动重试）"
                }
                return
            }
            $health = Parse-JsonBody $Result
            if ($null -eq $health -or -not [bool]$health.ok) {
                $script:ProbeInFlight = $false
                Set-Disconnected "健康检查失败"
                return
            }
            $authProp = $health.PSObject.Properties['authRequired']
            $authRequired = ($null -ne $authProp -and $authProp.Value -eq $true)
            if (-not $authRequired) {
                $script:ProbeInFlight = $false
                $script:AuthRole = "admin"
                $script:AdminAvailable = $true
                $script:ConnectionDetail = "已连接 · API 未启用鉴权"
                Update-ConnectionState $true
                return
            }

            $token = Get-EffectiveApiToken
            if ([string]::IsNullOrWhiteSpace($token)) {
                $script:ProbeInFlight = $false
                Set-Disconnected "未认证：环境变量 $([string]$cfg.apiTokenEnv) 未设置"
                return
            }
            $allowFallback = $true
            if ($null -ne $Op.Context -and $null -ne $Op.Context.PSObject.Properties['AllowSessionFallback']) {
                $allowFallback = [bool]$Op.Context.AllowSessionFallback
            }
            $ctx = [pscustomobject]@{ AllowSessionFallback = $allowFallback }
            if (-not (Start-CommanderGetAsync '/api/auth/check' $token 'probe-auth' $ctx)) {
                $script:ProbeInFlight = $false
                Set-Disconnected "未认证：无法创建认证请求"
            }
            return
        }
        'probe-auth' {
            if ([bool]$Result.Ok) {
                $check = Parse-JsonBody $Result
                $script:ProbeInFlight = $false
                [void](Apply-AuthCheck $check)
                return
            }

            $status = [int]$Result.StatusCode
            $kind = if ($null -ne $Result.PSObject.Properties['FailureKind']) { [string]$Result.FailureKind } else { "" }

            # Only authentication responses may invalidate a cached session token.
            # Network failures/timeouts do not prove that any token is wrong.
            if ($status -eq 401 -or $status -eq 403) {
                $allowFallback = $true
                if ($null -ne $Op.Context -and $null -ne $Op.Context.PSObject.Properties['AllowSessionFallback']) {
                    $allowFallback = [bool]$Op.Context.AllowSessionFallback
                }
                if ($allowFallback -and -not [string]::IsNullOrWhiteSpace([string]$script:SessionAdminToken)) {
                    $script:SessionAdminToken = ""
                    $script:ProbeInFlight = $false
                    Start-ConnectionProbe $false
                    return
                }
                $script:ProbeInFlight = $false
                Set-Disconnected "未认证：Token 无效或已变更"
                return
            }

            $script:ProbeInFlight = $false
            if ($kind -eq "timeout") {
                Set-Disconnected "请求超时（自动重试）"
            } elseif ($kind -eq "network") {
                Set-Disconnected "无法连接（自动重试）"
            } elseif ($status -ge 500) {
                Set-Disconnected "Commander 服务端错误（自动重试）"
            } else {
                Set-Disconnected "认证请求失败（自动重试）"
            }
            return
        }
        'state' {
            Handle-StateResult $Result
            return
        }
        'admin-verify' {
            $ctx = $Op.Context
            $verifyId = 0
            try { $verifyId = [int]$ctx.VerifyId } catch {}

            # A stale/duplicate completion must never speak for a newer Admin
            # verification attempt. This also prevents a late callback from
            # producing a second contradictory dialog.
            if ($verifyId -ne [int]$script:AdminVerifyGeneration) { return }

            # Consume this generation immediately. Any duplicate completion for
            # the same request id becomes stale before a dialog is shown.
            $script:AdminVerifyGeneration++
            $script:AdminActionInFlight = $false

            if (-not [bool]$Result.Ok) {
                $status = [int]$Result.StatusCode
                $kind = if ($null -ne $Result.PSObject.Properties['FailureKind']) { [string]$Result.FailureKind } else { "" }

                if ($status -eq 401) {
                    # Only an explicit authentication rejection is evidence that
                    # the supplied Admin Token is invalid.
                    $msg = "Admin Token 无效或已变更。"
                    $icon = [System.Windows.Forms.MessageBoxIcon]::Warning
                } elseif ($status -eq 403) {
                    $msg = "服务器已收到 Token，但拒绝 Admin 权限验证（HTTP 403）。"
                    $icon = [System.Windows.Forms.MessageBoxIcon]::Warning
                } elseif ($kind -eq "timeout") {
                    $msg = "请求超时；Commander 未返回验证结果。Admin Token 尚未验证。"
                    $icon = [System.Windows.Forms.MessageBoxIcon]::Information
                    Set-Disconnected "请求超时（自动重试）"
                } elseif ($kind -eq "network") {
                    $msg = "无法连接 Commander；Admin Token 尚未验证。"
                    if (-not [string]::IsNullOrWhiteSpace([string]$Result.Error)) { $msg += "`r`n$([string]$Result.Error)" }
                    $icon = [System.Windows.Forms.MessageBoxIcon]::Information
                    Set-Disconnected "无法连接（自动重试）"
                } elseif ($status -ge 500) {
                    $msg = "Commander 返回 HTTP $status；Admin Token 尚未验证。"
                    $icon = [System.Windows.Forms.MessageBoxIcon]::Error
                    Set-Disconnected "Commander 服务端错误（自动重试）"
                } elseif ($status -gt 0) {
                    $msg = "Commander 返回 HTTP $status；无法完成 Admin Token 验证，但这不代表 Token 错误。"
                    $icon = [System.Windows.Forms.MessageBoxIcon]::Error
                } else {
                    $detail = [string]$Result.Error
                    $msg = "未能完成 Admin Token 验证；Token 尚未验证。"
                    if (-not [string]::IsNullOrWhiteSpace($detail)) { $msg += "`r`n$detail" }
                    $icon = [System.Windows.Forms.MessageBoxIcon]::Information
                }
                [System.Windows.Forms.MessageBox]::Show($msg,"Commander 管理员授权",[System.Windows.Forms.MessageBoxButtons]::OK,$icon) | Out-Null
                return
            }

            $check = Parse-JsonBody $Result
            if ($null -eq $check) {
                [System.Windows.Forms.MessageBox]::Show("Commander 返回了无法解析的认证响应；Admin Token 尚未确认。","Commander 管理员授权",[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
                return
            }
            if (-not [bool]$check.authenticated) {
                [System.Windows.Forms.MessageBox]::Show("Commander 明确拒绝了该 Token。","Commander 管理员授权",[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
                return
            }
            if ([string]$check.role -ne "admin") {
                [System.Windows.Forms.MessageBox]::Show("该 Token 已通过认证，但权限为 $([string]$check.role)，不是 Admin。","Commander 管理员授权",[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
                return
            }

            $token = [string]$ctx.Token
            if ([string]$ctx.Mode -eq "session") {
                $script:SessionAdminToken = $token
                $script:AuthRole = "admin"
                $script:AdminAvailable = $true
                $script:ConnectionDetail = "已连接 · Admin（本次 Tray 会话）"
                Update-ConnectionState $true
            }
            Start-AdminPost ([string]$ctx.ActionLabel) ([string]$ctx.Path) $token
            return
        }
        'admin-post' {
            $script:AdminActionInFlight = $false
            $label = [string]$Op.Context.ActionLabel
            if ([bool]$Result.Ok) {
                $notify.ShowBalloonTip(3500,"Kimsufi Stock Commander","$label 已提交。",[System.Windows.Forms.ToolTipIcon]::Info)
                return
            }
            if (($Result.StatusCode -eq 401 -or $Result.StatusCode -eq 403) -and -not [string]::IsNullOrWhiteSpace([string]$script:SessionAdminToken)) {
                $script:SessionAdminToken = ""
                Start-ConnectionProbe
            }
            $kind = if ($null -ne $Result.PSObject.Properties['FailureKind']) { [string]$Result.FailureKind } else { "" }
            if ($kind -eq "timeout") {
                Set-Disconnected "请求超时（自动重试）"
            } elseif ($kind -eq "network") {
                Set-Disconnected "无法连接（自动重试）"
            } elseif ([int]$Result.StatusCode -ge 500) {
                Set-Disconnected "Commander 服务端错误（自动重试）"
            }
            $msg = Get-HttpFailureText $Result $label
            [System.Windows.Forms.MessageBox]::Show($msg,"Kimsufi Tray",[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
            return
        }
    }
}

function Process-PendingHttp {
    for ($i = $script:PendingHttp.Count - 1; $i -ge 0; $i--) {
        $op = $script:PendingHttp[$i]
        if ($null -eq $op.ContentTask) {
            if (-not $op.ResponseTask.IsCompleted) { continue }
            try {
                $op.Response = $op.ResponseTask.Result
                if ($null -ne $op.Response.Content) {
                    $op.ContentTask = $op.Response.Content.ReadAsStringAsync()
                    continue
                }
            } catch {
                $result = New-HttpFailureResult $_.Exception
                [void]$script:PendingHttp.RemoveAt($i)
                try { $op.Request.Dispose() } catch {}
                try { if ($null -ne $op.Response) { $op.Response.Dispose() } } catch {}
                try { Dispatch-HttpCompletion $op $result } catch { Set-Disconnected "Tray 网络回调异常" }
                continue
            }
        }

        if ($null -ne $op.ContentTask -and -not $op.ContentTask.IsCompleted) { continue }

        try {
            $body = if ($null -ne $op.ContentTask) { [string]$op.ContentTask.Result } else { '' }
            $statusCode = if ($null -ne $op.Response) { [int]$op.Response.StatusCode } else { 0 }
            $ok = ($null -ne $op.Response -and $op.Response.IsSuccessStatusCode)
            $result = [pscustomobject]@{ Ok = $ok; StatusCode = $statusCode; Body = $body; Error = ''; FailureKind = $(if ($ok) { '' } else { 'http' }) }
        } catch {
            $result = New-HttpFailureResult $_.Exception
        }

        [void]$script:PendingHttp.RemoveAt($i)
        try { $op.Request.Dispose() } catch {}
        try { if ($null -ne $op.Response) { $op.Response.Dispose() } } catch {}
        try { Dispatch-HttpCompletion $op $result } catch { Set-Disconnected "Tray 网络回调异常" }
    }
}

function Show-AdminElevationDialog([string]$ActionLabel) {
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "Commander 管理员授权"
    $form.StartPosition = "CenterScreen"
    $form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.ShowInTaskbar = $true
    $form.TopMost = $true
    $form.ClientSize = New-Object System.Drawing.Size(590, 300)

    $shieldPicture = New-Object System.Windows.Forms.PictureBox
    $shieldPicture.Size = New-Object System.Drawing.Size(32, 32)
    $shieldPicture.Location = New-Object System.Drawing.Point(22, 16)
    $shieldPicture.SizeMode = [System.Windows.Forms.PictureBoxSizeMode]::CenterImage
    try {
        $shieldPicture.Image = [System.Drawing.SystemIcons]::Shield.ToBitmap()
    } catch {
        # Older .NET Framework fallback: keep the layout stable even if Shield is unavailable.
        $shieldPicture.Image = [System.Drawing.SystemIcons]::Information.ToBitmap()
    }
    $form.Controls.Add($shieldPicture)

    $title = New-Object System.Windows.Forms.Label
    $title.Text = "Commander 管理员授权"
    $title.Font = New-Object System.Drawing.Font("Segoe UI", 13, [System.Drawing.FontStyle]::Bold)
    $title.AutoSize = $true
    $title.Location = New-Object System.Drawing.Point(62, 19)
    $form.Controls.Add($title)

    $message = New-Object System.Windows.Forms.Label
    $message.Text = "$ActionLabel 需要 Admin 权限。`r`n输入 Admin Token 后，可仅执行此次操作，或提升当前 Tray 会话。"
    $message.AutoSize = $false
    $message.Size = New-Object System.Drawing.Size(545, 48)
    $message.Location = New-Object System.Drawing.Point(24, 58)
    $form.Controls.Add($message)

    $tokenLabel = New-Object System.Windows.Forms.Label
    $tokenLabel.Text = "Admin Token"
    $tokenLabel.AutoSize = $true
    $tokenLabel.Location = New-Object System.Drawing.Point(24, 116)
    $form.Controls.Add($tokenLabel)

    $tokenBox = New-Object System.Windows.Forms.TextBox
    $tokenBox.Location = New-Object System.Drawing.Point(24, 138)
    $tokenBox.Size = New-Object System.Drawing.Size(540, 28)
    $tokenBox.UseSystemPasswordChar = $true
    $form.Controls.Add($tokenBox)

    $showToken = New-Object System.Windows.Forms.CheckBox
    $showToken.Text = "显示 Token"
    $showToken.AutoSize = $true
    $showToken.Location = New-Object System.Drawing.Point(24, 174)
    $showToken.add_CheckedChanged({
        $tokenBox.UseSystemPasswordChar = -not $showToken.Checked
    })
    $form.Controls.Add($showToken)

    $hint = New-Object System.Windows.Forms.Label
    $hint.Text = "Admin Token 不会写入 JSON。单次授权不会保存；会话提升只保留在当前 Tray 进程内存。"
    $hint.AutoSize = $false
    $hint.Size = New-Object System.Drawing.Size(545, 34)
    $hint.Location = New-Object System.Drawing.Point(24, 202)
    $form.Controls.Add($hint)

    $onceButton = New-Object System.Windows.Forms.Button
    $onceButton.Text = "仅执行此次"
    $onceButton.Size = New-Object System.Drawing.Size(118, 34)
    $onceButton.Location = New-Object System.Drawing.Point(24, 248)
    $form.Controls.Add($onceButton)

    $sessionButton = New-Object System.Windows.Forms.Button
    $sessionButton.Text = "提升为 Admin（本次会话）"
    $sessionButton.Size = New-Object System.Drawing.Size(230, 34)
    $sessionButton.Location = New-Object System.Drawing.Point(152, 248)
    $form.Controls.Add($sessionButton)

    $cancelButton = New-Object System.Windows.Forms.Button
    $cancelButton.Text = "取消"
    $cancelButton.Size = New-Object System.Drawing.Size(88, 34)
    $cancelButton.Location = New-Object System.Drawing.Point(476, 248)
    $cancelButton.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $form.Controls.Add($cancelButton)

    $commit = {
        param([string]$Mode)
        if ([string]::IsNullOrWhiteSpace([string]$tokenBox.Text)) {
            [System.Windows.Forms.MessageBox]::Show("请输入 Admin Token。","Commander 管理员授权",[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
            $tokenBox.Focus()
            return
        }
        $form.Tag = [pscustomobject]@{ Mode = $Mode; Token = [string]$tokenBox.Text }
        $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $form.Close()
    }

    $onceButton.add_Click({ & $commit "once" })
    $sessionButton.add_Click({ & $commit "session" })
    $form.AcceptButton = $onceButton
    $form.CancelButton = $cancelButton
    $form.add_Shown({ $tokenBox.Focus() })

    try {
        $dialogResult = $form.ShowDialog()
        if ($dialogResult -eq [System.Windows.Forms.DialogResult]::OK -and $null -ne $form.Tag) {
            return $form.Tag
        }
        return $null
    } finally {
        $form.Dispose()
    }
}

function Invoke-AdminAction([string]$ActionLabel, [string]$Path) {
    if (-not $script:Connected) {
        [System.Windows.Forms.MessageBox]::Show("Commander 当前未连接。","Kimsufi Tray",[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
        return
    }
    if ($script:AdminActionInFlight) {
        $notify.ShowBalloonTip(2500,"Kimsufi Stock Commander","已有管理员操作正在处理。",[System.Windows.Forms.ToolTipIcon]::Info)
        return
    }

    if ($script:AuthRole -eq "admin") {
        Start-AdminPost $ActionLabel $Path (Get-EffectiveApiToken)
        return
    }

    if (-not $script:AdminAvailable) {
        [System.Windows.Forms.MessageBox]::Show("服务器当前没有配置 Admin Token；此操作不可提权执行。","Kimsufi Tray",[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
        return
    }

    $grant = Show-AdminElevationDialog $ActionLabel
    if ($null -eq $grant) { return }

    $script:AdminActionInFlight = $true
    $script:AdminVerifyGeneration++
    $verifyId = [int]$script:AdminVerifyGeneration
    $notify.ShowBalloonTip(2200,"Kimsufi Stock Commander","正在验证 Admin Token…",[System.Windows.Forms.ToolTipIcon]::Info)
    $ctx = [pscustomobject]@{
        ActionLabel = $ActionLabel
        Path = $Path
        Token = [string]$grant.Token
        Mode = [string]$grant.Mode
        VerifyId = $verifyId
    }
    if (-not (Start-CommanderGetAsync '/api/auth/check' ([string]$grant.Token) 'admin-verify' $ctx)) {
        # Invalidate this attempt so a partially-created late callback cannot speak.
        if ($verifyId -eq [int]$script:AdminVerifyGeneration) { $script:AdminVerifyGeneration++ }
        $script:AdminActionInFlight = $false
        [System.Windows.Forms.MessageBox]::Show("无法创建 Admin Token 验证请求；Token 尚未验证。","Commander 管理员授权",[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
    }
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
$pollItem = $menu.Items.Add("🛡 立即轮询（需要 Admin）")
$catalogItem = $menu.Items.Add("🛡 刷新目录（需要 Admin）")
$demoteItem = $menu.Items.Add("🛡 降回 Read")
$demoteItem.Visible = $false
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
$pollItem.add_Click({ Invoke-AdminAction "立即轮询" '/api/poll' })
$catalogItem.add_Click({ Invoke-AdminAction "刷新目录" '/api/catalog/refresh' })
$demoteItem.add_Click({
    $script:AdminVerifyGeneration++
    $script:SessionAdminToken = ""
    $script:AuthRole = "read"
    $script:ConnectionDetail = "已连接 · Read"
    Update-ConnectionState $true
    $notify.ShowBalloonTip(3000,"Kimsufi Stock Commander","已降回 Read 权限。",[System.Windows.Forms.ToolTipIcon]::Info)
})

function Update-ConnectionState([bool]$Connected) {
    $changed = ($script:Connected -ne $Connected)
    $script:Connected = $Connected
    $statusItem.Text = "状态：$script:ConnectionDetail"

    $adminConnected = ($Connected -and $script:AuthRole -eq "admin")
    $readConnected = ($Connected -and $script:AuthRole -eq "read")

    if (-not $Connected) {
        $pollItem.Enabled = $false
        $catalogItem.Enabled = $false
        $pollItem.Text = "🛡 立即轮询（需要 Admin）"
        $catalogItem.Text = "🛡 刷新目录（需要 Admin）"
    } elseif ($adminConnected) {
        $pollItem.Enabled = $true
        $catalogItem.Enabled = $true
        $pollItem.Text = "🛡 立即轮询"
        $catalogItem.Text = "🛡 刷新目录"
    } elseif ($readConnected -and $script:AdminAvailable) {
        $pollItem.Enabled = $true
        $catalogItem.Enabled = $true
        $pollItem.Text = "🛡 立即轮询（需要 Admin）"
        $catalogItem.Text = "🛡 刷新目录（需要 Admin）"
    } else {
        $pollItem.Enabled = $false
        $catalogItem.Enabled = $false
        $pollItem.Text = "🛡 立即轮询（服务器未配置 Admin）"
        $catalogItem.Text = "🛡 刷新目录（服务器未配置 Admin）"
    }

    $demoteItem.Visible = ($adminConnected -and -not [string]::IsNullOrWhiteSpace([string]$script:SessionAdminToken))
    $demoteItem.Enabled = $demoteItem.Visible

    $notify.Text = if ($Connected) { "Kimsufi Stock Commander · connected" } else { "Kimsufi Stock Commander · disconnected" }
}

# Network completion pump. This timer never performs network I/O itself; it only
# consumes already-completed .NET Tasks and invokes their callbacks on the UI thread.
$networkPump = New-Object System.Windows.Forms.Timer
$networkPump.Interval = 100
$networkPump.add_Tick({ Process-PendingHttp })
$networkPump.Start()

# Periodic state polling is single-flight: if the previous request is still in
# flight, the next tick does nothing instead of stacking another request.
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = [Math]::Max(1000,[int]$cfg.notificationPollMs)
$timer.add_Tick({ Start-StatePoll })
$timer.Start()

# Initial connection/auth probe is asynchronous so a dead backend cannot block
# the Tray message loop during normal operation.
Start-ConnectionProbe

$exitTrayItem.add_Click({
    $timer.Stop(); $networkPump.Stop(); try { $script:HttpClient.Dispose() } catch {}; $notify.Visible=$false; $notify.Dispose(); Release-TrayInstanceMutex; [System.Windows.Forms.Application]::Exit()
})
if ($stopItem) {
    $stopItem.add_Click({
        $timer.Stop()
        $networkPump.Stop()
        try { $script:HttpClient.Dispose() } catch {}
        if ($script:StartedBackend -and -not $script:StartedBackend.HasExited) {
            try { Stop-Process -Id $script:StartedBackend.Id -Force } catch {}
        }
        $notify.Visible=$false; $notify.Dispose(); Release-TrayInstanceMutex; [System.Windows.Forms.Application]::Exit()
    })
}

try {
    [System.Windows.Forms.Application]::Run()
} finally {
    Release-TrayInstanceMutex
}
