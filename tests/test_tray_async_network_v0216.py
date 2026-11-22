from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S = (ROOT / "KimsufiTray.ps1").read_text(encoding="utf-8-sig")

assert 'Add-Type -AssemblyName System.Net.Http' in S
assert '$script:HttpClient = New-Object System.Net.Http.HttpClient' in S
assert '$script:HttpClient.Timeout = [TimeSpan]::FromSeconds' in S
assert '$script:PendingHttp = New-Object System.Collections.ArrayList' in S
assert 'function Start-HttpRequest' in S
assert 'function Process-PendingHttp' in S
assert 'function Start-ConnectionProbe' in S
assert 'function Start-StatePoll' in S
assert 'function Dispatch-HttpCompletion' in S
assert "'probe-health'" in S and "'probe-auth'" in S and "'admin-verify'" in S and "'admin-post'" in S
assert 'GetNewClosure' not in S
assert 'function Start-CommanderGetAsync' in S
assert 'function Start-CommanderPostAsync' in S
assert '$script:StatePollInFlight = $false' in S
assert '$script:ProbeInFlight = $false' in S
assert '$script:AdminActionInFlight = $false' in S
assert '$networkPump.Interval = 100' in S
assert '$timer.add_Tick({ Start-StatePoll })' in S

# Runtime UI callbacks must not block on Invoke-RestMethod.
admin_start = S.index('function Invoke-AdminAction')
admin_end = S.index('try { Start-CommanderBackend }', admin_start)
admin_block = S[admin_start:admin_end]
assert 'Invoke-RestMethod' not in admin_block
assert 'Test-CommanderSync' not in admin_block
assert 'Start-CommanderGetAsync' in admin_block
assert 'Start-AdminPost' in admin_block

pump_start = S.index('# Network completion pump.')
exit_start = S.index('$exitTrayItem.add_Click({', pump_start)
runtime_block = S[pump_start:exit_start]
assert 'Invoke-RestMethod' not in runtime_block
assert 'Start-StatePoll' in runtime_block

# Synchronous network helpers are retained only for the pre-UI local-backend startup path.
assert 'function Invoke-CommanderGetSync' in S
assert 'function Test-CommanderSync' in S
start_backend = S[S.index('function Start-CommanderBackend'):S.index('function Start-HttpRequest')]
assert 'Test-CommanderSync' in start_backend

# Demotion is local and must not wait for the backend.
demote = S[S.index('$demoteItem.add_Click({'):S.index('function Update-ConnectionState')]
assert 'Test-Commander' not in demote
assert 'Invoke-RestMethod' not in demote
assert '$script:SessionAdminToken = ""' in demote

print('v0.21.6 Tray async network regression: OK')