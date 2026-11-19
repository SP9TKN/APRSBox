# Turns the APRSBox station's watchdog off for good: removes the "APRSBox-Watchdog" scheduled task
# (so neither the logon trigger nor the 5-minute supervisor can bring it back) and kills every
# running copy. The programs themselves (direwolf, Audio PTT, APRSBox) are NOT touched.
# Undo with start-watchdog.ps1.

. "$PSScriptRoot\WindowUtils.ps1"   # Get-AprsProcess (only for the final status line)

$taskName = 'APRSBox-Watchdog'
$watchdog = Join-Path $PSScriptRoot 'aprsbox-watchdog.ps1'

# By full path: the older stations have their own aprs-watchdog.ps1 scripts.
function Get-WatchdogProcesses {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$watchdog*" }
}

$failed = $false

# Task first, process second: otherwise the supervisor trigger could relaunch what we just killed.
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    try {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        Write-Host "Zadanie '$taskName' usuniete - watchdog nie wroci przy logowaniu."
    } catch {
        Write-Error "Nie udalo sie usunac zadania '$taskName': $($_.Exception.Message)`nUruchom stop-watchdog jako administrator."
        $failed = $true
    }
} else {
    Write-Host "Zadania '$taskName' nie ma - nic do usuniecia."
}

# A few rounds, in case a copy was starting up while we were killing.
$sw = [Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt 10) {
    $procs = @(Get-WatchdogProcesses)
    if (-not $procs) { break }
    foreach ($p in $procs) {
        Write-Host "Zatrzymuje watchdoga (PID $($p.ProcessId))..."
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500
}

if (Get-WatchdogProcesses) {
    Write-Error "Watchdog nadal dziala - nie udalo sie go zatrzymac."
    $failed = $true
} else {
    Write-Host "Watchdog zatrzymany."
}

$running = 'direwolf', 'AudioPTT', 'APRSBox' | Where-Object { Get-AprsProcess -ProcessName $_ }
if ($running) { Write-Host "Programy stacji dzialaja dalej: $($running -join ', ')." }

exit ([int]$failed)
