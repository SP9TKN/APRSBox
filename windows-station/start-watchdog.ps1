# Turns the APRSBox station's watchdog on for good: registers the "APRSBox-Watchdog" scheduled
# task (logon trigger + a 5-minute supervisor repeat, the same scheme as the older stations' tasks)
# and starts it now.
# Safe to run repeatedly - the task is re-registered and a running watchdog is left alone.
# Undo with stop-watchdog.ps1.

$taskName = 'APRSBox-Watchdog'
$watchdog = Join-Path $PSScriptRoot 'aprsbox-watchdog.ps1'
$logPath  = Join-Path $PSScriptRoot 'aprsbox-watchdog.log'

# By full path: the older stations have their own aprs-watchdog.ps1 scripts.
function Get-WatchdogProcesses {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$watchdog*" -and $_.CommandLine -notlike '*-Once*' }
}

if (-not (Test-Path $watchdog)) {
    Write-Error "Brak pliku $watchdog"
    exit 1
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userSid  = $identity.User.Value
$userName = $identity.Name
$taskArgs = [Security.SecurityElement]::Escape("-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdog`"")
$now      = Get-Date -Format "yyyy-MM-dd'T'HH:mm:ssK"

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Stacja APRSBox: pilnuje, zeby direwolf, Audio PTT i APRSBox byly zawsze uruchomione, a ich okna staly tam, gdzie mowi WindowUtils.ps1 / layout.json (AprsBOX\windows-station; start: start-watchdog, stop: stop-watchdog).</Description>
    <URI>\$taskName</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$userSid</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Priority>4</Priority>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <LogonTrigger>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>true</StopAtDurationEnd>
      </Repetition>
      <UserId>$userName</UserId>
    </LogonTrigger>
    <TimeTrigger>
      <StartBoundary>$now</StartBoundary>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>true</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>$taskArgs</Arguments>
    </Exec>
  </Actions>
</Task>
"@

try {
    Register-ScheduledTask -TaskName $taskName -Xml $xml -Force -ErrorAction Stop | Out-Null
} catch {
    Write-Error "Nie udalo sie zarejestrowac zadania '$taskName': $($_.Exception.Message)`nUruchom start-watchdog jako administrator."
    exit 1
}
Write-Host "Zadanie '$taskName' zarejestrowane (start przy logowaniu + kontrola co 5 min)."

if (Get-WatchdogProcesses) {
    Write-Host "Watchdog juz dziala - zostawiam."
} else {
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 10 -and -not (Get-WatchdogProcesses)) { Start-Sleep -Milliseconds 300 }
}

if (Get-WatchdogProcesses) {
    Write-Host "Watchdog dziala (programy co 30 s, uklad okien co 5 s). Log: $logPath"
    exit 0
}
Write-Error "Zadanie uruchomione, ale proces watchdoga sie nie pojawil - zajrzyj do $logPath"
exit 1
