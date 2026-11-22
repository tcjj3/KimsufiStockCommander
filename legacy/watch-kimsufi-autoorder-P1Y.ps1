$ErrorActionPreference = "Stop"

$exe = "$env:USERPROFILE\go\bin\kimsufi-notifier.exe"

# Polling cadence.
$interval = 30
$heartbeatHours = 6

# Legacy reference script. Auto-order is DISABLED by default in the public repository.
# Review every target and safety setting before enabling it manually.
$autoOrderEnabled = $false

$eventsCsv = Join-Path $PSScriptRoot "kimsufi-events.csv"
$errorLog  = Join-Path $PSScriptRoot "kimsufi-errors.log"
$orderLog  = Join-Path $PSScriptRoot "kimsufi-order.log"

# Persistent safety markers.
# success.lock: once an order is confirmed, any future run exits immediately.
# review.lock: an ambiguous/non-stock order failure occurred; manual review is required.
$orderSuccessLock = Join-Path $PSScriptRoot "ks5-order-success.lock"
$orderReviewLock  = Join-Path $PSScriptRoot "ks5-order-review.lock"

# This file is only used as an OS-level exclusive file handle.
# Its mere existence does NOT mean an order is running.
$orderActiveGuard = Join-Path $PSScriptRoot "ks5-order-active.guard"

$targets = @(
    @{
        Name = "P0 KS-5 64GB + 2x450GB NVMe"
        AutoOrder = $true

        Args = @(
            "check",
            "--plan-code", "24sk50-v1",
            "--endpoint", "ovh-eu",
            "--option", "memory=ram-64g-ecc-2400,storage=softraid-2x450nvme"
        )

        OrderArgs = @(
            "order",
            "--plan-code", "24sk50-v1",
            "--endpoint", "ovh-eu",
            "--country", "IE",
            "--datacenters", "any",
            "--item-option", "memory=ram-64g-ecc-2400-24sk50-v1",
            "--item-option", "storage=softraid-2x450nvme-24sk50-v1",
            "--quantity", "1",
            "--price-duration", "P1Y",
            "--price-mode", "upfront12",
            "--auto-pay"
        )
    },
    @{
        Name = "P1 KS-5 32GB + 2x450GB NVMe"
        AutoOrder = $false

        Args = @(
            "check",
            "--plan-code", "24sk50-v1",
            "--endpoint", "ovh-eu",
            "--option", "memory=ram-32g-ecc-2400,storage=softraid-2x450nvme"
        )
    },
    @{
        Name = "KS-B 32GB + 120GB SSD"
        AutoOrder = $false

        Args = @(
            "check",
            "--plan-code", "25skb012",
            "--endpoint", "ovh-eu"
        )
    }
)

$state = @{}
$availableSince = @{}
$lastDc = @{}
$lastHeartbeat = Get-Date

function Write-Event {
    param(
        [string]$Event,
        [string]$Target,
        [string]$Datacenters = "",
        [string]$PreviousDatacenters = "",
        [Nullable[int]]$ObservedSpanSeconds = $null,
        [string]$Note = ""
    )

    $row = [pscustomobject]@{
        Timestamp            = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        Event                = $Event
        Target               = $Target
        Datacenters          = $Datacenters
        PreviousDatacenters  = $PreviousDatacenters
        ObservedSpanSeconds  = if ($null -eq $ObservedSpanSeconds) { "" } else { $ObservedSpanSeconds }
        PollIntervalSeconds  = $interval
        Note                 = $Note
    }

    $row | Export-Csv -Path $eventsCsv -Append -NoTypeInformation -Encoding UTF8
}

function Write-OrderLog {
    param(
        [string]$Target,
        [int]$ExitCode,
        [string[]]$Lines
    )

    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $header = @"
============================================================
$stamp
Target: $Target
ExitCode: $ExitCode
------------------------------------------------------------
"@

    Add-Content -Path $orderLog -Value $header -Encoding UTF8
    if ($null -ne $Lines -and $Lines.Count -gt 0) {
        Add-Content -Path $orderLog -Value ($Lines -join "`r`n") -Encoding UTF8
    }
    Add-Content -Path $orderLog -Value "`r`n" -Encoding UTF8
}

function Parse-Availability {
    param(
        [object[]]$Output
    )

    $lines = @($Output | ForEach-Object { "$_" })

    # Without -h/-hh, datacenters stay as compact OVH codes: fra, gra, rbx, ...
    $availableLine = $lines |
        Where-Object { $_ -match '\savailable\s+' } |
        Select-Object -First 1

    if ($null -ne $availableLine) {
        $dc = ""
        if ($availableLine -match '\savailable\s+(.+?)\s*$') {
            $dc = $Matches[1].Trim()
        }

        return [pscustomobject]@{
            State = "available"
            DC    = $dc
            Raw   = ($lines -join " | ")
        }
    }

    $unavailableLine = $lines |
        Where-Object { $_ -match '\sunavailable(?:\s|$)' } |
        Select-Object -First 1

    if ($null -ne $unavailableLine) {
        return [pscustomobject]@{
            State = "unavailable"
            DC    = ""
            Raw   = ($lines -join " | ")
        }
    }

    # Never convert API/network/tool errors into a false SOLD_OUT event.
    return [pscustomobject]@{
        State = "error"
        DC    = ""
        Raw   = ($lines -join " | ")
    }
}

function Alert-Available {
    param([string]$Text)

    Write-Host $Text -ForegroundColor Green
    try {
        [console]::Beep(1200, 700)
        Start-Sleep -Milliseconds 150
        [console]::Beep(1600, 1000)
    }
    catch {
        # Notification logging still works if Console.Beep is unavailable.
    }
}

function Test-AutoOrderPrerequisites {
    if (-not $autoOrderEnabled) {
        return
    }

    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "kimsufi-notifier executable not found: $exe"
    }

    if (Test-Path -LiteralPath $orderSuccessLock) {
        Write-Host "SUCCESS lock exists: $orderSuccessLock" -ForegroundColor Green
        Write-Host "A KS-5 order was already confirmed. Exiting to prevent duplicate purchase." -ForegroundColor Green
        exit 0
    }

    if (Test-Path -LiteralPath $orderReviewLock) {
        Write-Host "REVIEW lock exists: $orderReviewLock" -ForegroundColor Red
        Write-Host "Automatic ordering is disarmed until you inspect the order log and remove the review lock." -ForegroundColor Red
        exit 2
    }

    $requiredEnv = @(
        "OVH_APP_KEY",
        "OVH_APP_SECRET",
        "OVH_CONSUMER_KEY"
    )

    $missing = @(
        $requiredEnv | Where-Object {
            [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_))
        }
    )

    if ($missing.Count -gt 0) {
        Write-Host "Automatic ordering is NOT armed." -ForegroundColor Red
        Write-Host "Missing environment variable(s): $($missing -join ', ')" -ForegroundColor Red
        Write-Host "Set them first, open a new shell if you used setx, then restart this script." -ForegroundColor Yellow
        exit 3
    }
}

function Invoke-AutoOrder {
    param(
        [hashtable]$Target
    )

    if (-not $autoOrderEnabled) {
        return "disabled"
    }

    if (-not $Target.ContainsKey("AutoOrder") -or -not $Target["AutoOrder"]) {
        return "disabled"
    }

    # Persistent duplicate-purchase protection.
    if (Test-Path -LiteralPath $orderSuccessLock) {
        return "success"
    }

    if (Test-Path -LiteralPath $orderReviewLock) {
        return "review"
    }

    # Atomic cross-process guard.
    # OpenOrCreate + FileShare.None means only one watcher can execute the
    # actual order command at a time. A crashed process automatically releases
    # the OS handle, so the .guard file itself never has to be deleted.
    $guardStream = $null
    try {
        $guardStream = [System.IO.File]::Open(
            $orderActiveGuard,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }
    catch [System.IO.IOException] {
        Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') AUTO-ORDER skipped: another watcher holds the order guard." -ForegroundColor Yellow
        return "busy"
    }

    try {
        # Re-check persistent markers after acquiring the guard to close the race
        # between two watcher processes.
        if (Test-Path -LiteralPath $orderSuccessLock) {
            return "success"
        }

        if (Test-Path -LiteralPath $orderReviewLock) {
            return "review"
        }

        $now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Host "$now AUTO-ORDER attempting $($Target.Name)..." -ForegroundColor Magenta

        $orderOutput = @(
            & $exe @($Target["OrderArgs"]) 2>&1
        )
        $exitCode = $LASTEXITCODE
        $orderLines = @($orderOutput | ForEach-Object { "$_" })
        $text = $orderLines -join "`r`n"

        $orderLines | ForEach-Object {
            Write-Host $_
        }

        Write-OrderLog -Target $Target.Name -ExitCode $exitCode -Lines $orderLines

        # Positive confirmation from kimsufi-notifier's checkout path.
        $completedLine = $orderLines |
            Where-Object { $_ -match 'order completed:' } |
            Select-Object -First 1

        if ($null -ne $completedLine) {
            $successText = @"
SUCCESS
Time=$now
Target=$($Target.Name)
ExitCode=$exitCode
Confirmation=$completedLine
"@

            Set-Content -Path $orderSuccessLock -Value $successText -Encoding UTF8

            Write-Event -Event "ORDER_SUCCESS" -Target $Target.Name `
                -Note $completedLine

            Write-Host "$now AUTO-ORDER SUCCESS. Permanent success lock written." -ForegroundColor Green
            Write-Host "Watcher will exit now to prevent any duplicate purchase." -ForegroundColor Green

            try {
                [console]::Beep(1800, 1200)
                Start-Sleep -Milliseconds 150
                [console]::Beep(2200, 1500)
            }
            catch {}

            return "success"
        }

        # A pure stock race is safe to retry: check saw stock, but checkout found
        # every attempted datacenter unavailable before an order completed.
        $hasNotAvailable = ($text -match '(?im)datacenter\s+\S+\s+not available')
        $hasExplicitError = ($text -match '(?im)^\s*>\s*error:')

        if (($exitCode -eq 0) -and $hasNotAvailable -and -not $hasExplicitError) {
            Write-Event -Event "ORDER_MISS" -Target $Target.Name `
                -Note "Availability race lost at checkout; safe to retry on the next polling cycle."

            Write-Host "$now AUTO-ORDER stock race lost; retrying on the next polling cycle." -ForegroundColor Yellow
            return "miss"
        }

        # Anything else is deliberately treated as ambiguous:
        # auth/payment/API/network/config errors are NOT auto-retried because
        # retrying an uncertain checkout can create a duplicate paid server.
        $reviewText = @"
REVIEW REQUIRED
Time=$now
Target=$($Target.Name)
ExitCode=$exitCode

Automatic ordering has been disarmed because the result was not a clean
"not available" stock race and no "order completed:" confirmation was seen.

Inspect:
$orderLog

Last output:
$text
"@

        Set-Content -Path $orderReviewLock -Value $reviewText -Encoding UTF8

        Write-Event -Event "ORDER_REVIEW" -Target $Target.Name `
            -Note "Ambiguous/non-stock order failure; automatic ordering disarmed."

        Write-Host "$now AUTO-ORDER entered REVIEW state." -ForegroundColor Red
        Write-Host "No automatic retry will occur until the review lock is removed manually." -ForegroundColor Red

        try {
            [console]::Beep(900, 1200)
            Start-Sleep -Milliseconds 200
            [console]::Beep(700, 1500)
        }
        catch {}

        return "review"
    }
    finally {
        if ($null -ne $guardStream) {
            $guardStream.Dispose()
        }
    }
}

Test-AutoOrderPrerequisites

Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') watcher started; poll target = ${interval}s"
Write-Host "P0 AUTO-ORDER: LIVE / quantity=1 / country=IE / datacenters=any / P1Y+upfront12 / auto-pay" -ForegroundColor Magenta

while ($true) {
    # If another watcher succeeded or entered review state, this process must
    # stop as well even if the target is no longer currently AVAILABLE.
    if (Test-Path -LiteralPath $orderSuccessLock) {
        Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') success lock detected; exiting." -ForegroundColor Green
        exit 0
    }

    if (Test-Path -LiteralPath $orderReviewLock) {
        Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') review lock detected; exiting." -ForegroundColor Red
        exit 2
    }

    $cycleStart = Get-Date

    foreach ($target in $targets) {
        $output = & $exe @($target.Args) 2>&1
        $result = Parse-Availability -Output $output
        $nowObj = Get-Date
        $now = $nowObj.ToString("yyyy-MM-dd HH:mm:ss")

        if ($result.State -eq "error") {
            $line = "$now`tERROR`t$($target.Name)`t$($result.Raw)"
            Add-Content -Path $errorLog -Value $line -Encoding UTF8
            Write-Host "$now ERROR $($target.Name)" -ForegroundColor Red
            continue
        }

        $isInitial = -not $state.ContainsKey($target.Name)

        if ($isInitial) {
            $state[$target.Name] = $result.State
            $lastDc[$target.Name] = $result.DC

            if ($result.State -eq "available") {
                $availableSince[$target.Name] = $nowObj
                Alert-Available "$now INIT-AVAILABLE $($target.Name) [$($result.DC)]"
                Write-Event -Event "INIT_AVAILABLE" -Target $target.Name -Datacenters $result.DC `
                    -Note "Watcher started while target was already available; true availability start is unknown."
            }
            else {
                Write-Host "$now INIT $($target.Name): unavailable"
            }
        }
        else {
            $oldState = $state[$target.Name]

            if ($oldState -ne $result.State) {
                $state[$target.Name] = $result.State

                if ($result.State -eq "available") {
                    $availableSince[$target.Name] = $nowObj
                    $lastDc[$target.Name] = $result.DC

                    Alert-Available "$now AVAILABLE $($target.Name) [$($result.DC)]"
                    Write-Event -Event "AVAILABLE" -Target $target.Name -Datacenters $result.DC
                }
                else {
                    $dc = $lastDc[$target.Name]
                    $span = $null

                    if ($availableSince.ContainsKey($target.Name)) {
                        $span = [int](($nowObj - $availableSince[$target.Name]).TotalSeconds)
                        $availableSince.Remove($target.Name)
                    }

                    Write-Host "$now SOLD OUT $($target.Name) [$dc] observed-span=${span}s" -ForegroundColor Yellow
                    Write-Event -Event "SOLD_OUT" -Target $target.Name -Datacenters $dc `
                        -ObservedSpanSeconds $span `
                        -Note "Observed span is detection-to-detection, not exact stock lifetime; boundary uncertainty is approximately one polling cycle."

                    $lastDc[$target.Name] = ""
                }
            }
            elseif (($result.State -eq "available") -and ($lastDc[$target.Name] -ne $result.DC)) {
                # State can remain AVAILABLE while the set of datacenters changes.
                $previousDc = $lastDc[$target.Name]
                $lastDc[$target.Name] = $result.DC

                Write-Host "$now DC CHANGE $($target.Name): [$previousDc] -> [$($result.DC)]" -ForegroundColor Cyan
                Write-Event -Event "DC_CHANGE" -Target $target.Name `
                    -Datacenters $result.DC -PreviousDatacenters $previousDc
            }
        }

        # P0 is attempted whenever it is still reported AVAILABLE, not just on
        # the first transition. This matters if check sees stock but the first
        # checkout loses a clean stock race.
        if (($result.State -eq "available") -and
            $target.ContainsKey("AutoOrder") -and
            $target["AutoOrder"]) {

            $orderResult = Invoke-AutoOrder -Target $target

            if ($orderResult -eq "success") {
                exit 0
            }

            if ($orderResult -eq "review") {
                exit 2
            }
        }
    }

    if (((Get-Date) - $lastHeartbeat).TotalHours -ge $heartbeatHours) {
        Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') watcher alive"
        $lastHeartbeat = Get-Date
    }

    # Keep the polling round close to $interval seconds from cycle start,
    # instead of sleeping $interval seconds after API calls finish.
    $elapsed = ((Get-Date) - $cycleStart).TotalSeconds
    $sleepSeconds = [Math]::Max(1, [Math]::Ceiling($interval - $elapsed))
    Start-Sleep -Seconds $sleepSeconds
}