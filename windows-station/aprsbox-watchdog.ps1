param(
    [switch]$Once,     # run a single check pass and exit (diagnostics)
    [switch]$DryRun    # log what would be started or moved, start and move nothing
)

# Watchdog of the APRSBox station (dumb direwolf 1.8.1 + Audio PTT + APRSBox). It does two things:
#  1) every $checkSec seconds it checks that direwolf, Audio PTT and APRSBox are running. A missing
#     one is launched the way start-aprsbox.ps1 does it (same order), then the windows are placed
#     by fix-layout.ps1 and APRSBox is raised. Not while stop-aprsbox's aprsbox-stopped.flag
#     exists: a station stopped on purpose stays stopped until start-aprsbox;
#  2) every $layoutSec seconds it checks that each window is where $AprsLayout (WindowUtils.ps1,
#     or layout.json written by save-layout) says, and puts back the ones that are not - moved or
#     resized by hand, maximized, or shifted by Windows. Minimized windows are left alone: that is
#     the user's choice (also Win+D). direwolf hides its title bar under Audio PTT's bottom edge,
#     so a direwolf that came up over Audio PTT is laid back under it. Paused while edit-layout's
#     layout-edit.flag exists (at most 15 min), so the user can arrange the windows.
#
# Run by the "APRSBox-Watchdog" scheduled task (registered by start-watchdog.ps1, removed by
# stop-watchdog.ps1). A second copy exits at once on the mutex.
#
# Programs are launched through WMI (Win32_Process.Create) instead of Start-Process so they are
# not children of the scheduled task: stopping the task (stop-watchdog) must not take the station
# down with it.

. "$PSScriptRoot\WindowUtils.ps1"

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class AprsBoxGuard {
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
}
"@ -ErrorAction SilentlyContinue

$logPath       = Join-Path $PSScriptRoot "aprsbox-watchdog.log"
$fixLayoutPath = Join-Path $PSScriptRoot "fix-layout.ps1"
$checkSec      = 30    # how often the programs are checked
$layoutSec     = 5     # how often the window positions are checked
$tolerancePx   = 2     # a window this close to its place counts as in place (Qt may land 1 px off)
$startupGrace  = 20    # let logon and the virtual monitor settle before the first check
$layoutW       = 1920  # $AprsLayout rects are computed for a full 1920x1080 desktop
$layoutH       = 1080

# Same order as start-aprsbox.ps1: Audio PTT reads PTT from the COM port direwolf holds, and
# APRSBox connects to direwolf's KISS port, so direwolf goes first. APRSBox starts slowest.
$services = @(
    @{ Name = 'direwolf';  Proc = 'direwolf'; Exe = $AprsExe['direwolf']; TimeoutSec = 30 },
    @{ Name = 'Audio PTT'; Proc = 'AudioPTT'; Exe = $AprsExe['AudioPTT']; TimeoutSec = 30 },
    @{ Name = 'APRSBox';   Proc = 'APRSBox';  Exe = $AprsExe['APRSBox'];  TimeoutSec = 60 }
)

function Write-WatchdogLog {
    param([string]$Text)
    try {
        if ((Test-Path $logPath) -and (Get-Item $logPath).Length -gt 1MB) {
            Move-Item $logPath "$logPath.old" -Force
        }
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Text" | Out-File -FilePath $logPath -Append -Encoding UTF8
    } catch {}
}

function Test-FullScreen {
    [AprsBoxGuard]::GetSystemMetrics(0) -ge $layoutW -and [AprsBoxGuard]::GetSystemMetrics(1) -ge $layoutH
}

# "X,Y WxH" (Get-WindowRectText) within $tolerancePx of the layout rect on every edge.
function Test-RectClose {
    param([string]$RectText, $Pos)
    if ($RectText -notmatch '^(-?\d+),(-?\d+) (\d+)x(\d+)$') { return $false }
    $values = [int]$Matches[1], [int]$Matches[2], [int]$Matches[3], [int]$Matches[4]
    $wanted = $Pos.X, $Pos.Y, $Pos.W, $Pos.H
    for ($i = 0; $i -lt 4; $i++) {
        if ([Math]::Abs($values[$i] - $wanted[$i]) -gt $tolerancePx) { return $false }
    }
    $true
}

function Test-ServiceRunning {
    param($Service)
    [bool](Get-AprsProcess -ProcessName $Service.Proc)
}

# Returns the new process id, or 0 on failure. WMI-created processes run in the caller's
# interactive session but outside the scheduled task's process tree.
function Start-Detached {
    param($Service)
    $commandLine = '"' + $Service.Exe + '"'
    $arguments = $AprsArgs[$Service.Proc]
    if ($arguments) { $commandLine += ' ' + ($arguments -join ' ') }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine      = $commandLine
        CurrentDirectory = Split-Path $Service.Exe
    }
    if ($r.ReturnValue -ne 0) {
        Write-WatchdogLog "  BLAD: Win32_Process.Create dla $($Service.Name) zwrocil $($r.ReturnValue)"
        return 0
    }
    [int]$r.ProcessId
}

function Start-AprsService {
    param($Service)

    if (-not (Test-Path $Service.Exe)) {
        Write-WatchdogLog "BLAD: brak pliku $($Service.Exe) - $($Service.Name) nie zostanie uruchomiony"
        return $false
    }

    Write-WatchdogLog "brak $($Service.Name) - uruchamiam"
    if ($DryRun) { Write-WatchdogLog "  [DryRun] pomijam faktyczne uruchomienie"; return $false }

    if (-not (Start-Detached $Service)) { return $false }
    try {
        Wait-MainWindow -ProcessName $Service.Proc -TimeoutSec $Service.TimeoutSec | Out-Null
    } catch {
        Write-WatchdogLog "  OSTRZEZENIE: $($_.Exception.Message)"
        return $false
    }
    Write-WatchdogLog "  $($Service.Name) uruchomiony"
    $true
}

# Raises a window to the top of the normal stacking band without activating it. WindowUtils'
# Move-WindowToTop (AppActivate) needs foreground rights, which this hidden background process
# does not have. Toggling TOPMOST -> NOTOPMOST needs no foreground rights and does not steal focus.
function Move-WindowToFront {
    param([Parameter(Mandatory = $true)][IntPtr]$Handle)
    $flags = 0x0013   # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
    [AprsBoxGuard]::SetWindowPos($Handle, [IntPtr](-1), 0, 0, 0, 0, $flags) | Out-Null   # HWND_TOPMOST
    [AprsBoxGuard]::SetWindowPos($Handle, [IntPtr](-2), 0, 0, 0, 0, $flags) | Out-Null   # HWND_NOTOPMOST
}

# After a restart only: the windows from fix-layout.ps1, direwolf and Audio PTT to the bottom
# (direwolf under Audio PTT), APRSBox raised. Raising APRSBox periodically would pull it over
# whatever the user works in.
function Restore-Layout {
    if (-not (Test-FullScreen)) {
        Write-WatchdogLog "  pomijam uklad okien - pulpit mniejszy niz ${layoutW}x${layoutH}, ustawie go po powrocie monitora"
        return
    }
    try {
        & $fixLayoutPath *>&1 | ForEach-Object { Write-WatchdogLog "  $_" }
        Set-AprsBottomOrder
        $box = Get-AprsWindow -ProcessName 'APRSBox'
        if ($box) { Move-WindowToFront -Handle $box.Handle }
    } catch {
        Write-WatchdogLog "  BLAD ukladu okien: $($_.Exception.Message)"
    }
}

$pausedLogged = $false
function Invoke-Check {
    if (Test-Path $AprsStopFlag) {
        if (-not $script:pausedLogged) {
            Write-WatchdogLog "stacja zatrzymana przez stop-aprsbox - nic nie uruchamiam az do start-aprsbox"
            $script:pausedLogged = $true
        }
        return
    }
    if ($script:pausedLogged) {
        Write-WatchdogLog "flaga zatrzymania usunieta - znow pilnuje stacji"
        $script:pausedLogged = $false
    }
    $started = $false
    foreach ($s in $services) {
        if (Test-ServiceRunning $s) { continue }
        if (Start-AprsService $s) { $started = $true }
    }
    if ($started) { Restore-Layout }
}

# Window -> rect the layout check accepted although it differs from $AprsLayout. A program that
# will not take the exact rect (e.g. its own minimum size) is put back once, not every
# $layoutSec seconds forever; moving that window again makes it a candidate again.
$settledRect = @{}
$layoutStamp = $null

# Puts back every station window that is not where $AprsLayout says, without activating it.
# Silent on a smaller desktop (monitor off, RDP) - the windows are put back once the full monitor
# is back.
function Invoke-LayoutCheck {
    if (-not (Test-FullScreen) -or (Test-LayoutEditing)) { return }

    # A new layout.json from save-layout applies without restarting the watchdog.
    $stamp = if (Test-Path $AprsLayoutPath) { (Get-Item $AprsLayoutPath).LastWriteTimeUtc } else { $null }
    if ($stamp -ne $script:layoutStamp) {
        if ($null -ne $script:layoutStamp -or $null -ne $stamp) { Import-AprsLayout }
        if ($null -ne $script:layoutStamp) { Write-WatchdogLog "nowy uklad okien z layout.json" }
        $script:layoutStamp = $stamp
        $settledRect.Clear()
    }

    foreach ($name in $AprsLayout.Keys) {
        $win = Get-AprsWindow -ProcessName $name
        if (-not $win -or [Win32AprsBox]::IsIconic($win.Handle)) { continue }

        $pos    = $AprsLayout[$name]
        $wanted = "$($pos.X),$($pos.Y) $($pos.W)x$($pos.H)"
        $rect   = Get-WindowRectText -Handle $win.Handle
        $key    = "$name/$($win.Handle)"
        if (-not $rect -or (Test-RectClose $rect $pos) -or $rect -eq $settledRect[$key]) { continue }

        Write-WatchdogLog "okno $name nie na miejscu: $rect zamiast $wanted - ustawiam"
        if ($DryRun) { Write-WatchdogLog "  [DryRun] pomijam przesuniecie"; continue }
        Set-WindowPosition -Handle $win.Handle -X $pos.X -Y $pos.Y -W $pos.W -H $pos.H
        Start-Sleep -Milliseconds 500   # SetWindowPos is asynchronous (SWP_ASYNCWINDOWPOS)
        $after = Get-WindowRectText -Handle $win.Handle
        if ($after -and -not (Test-RectClose $after $pos)) {
            Write-WatchdogLog "  $name nie przyjal dokladnie tego miejsca, zostal na $after - nie poprawiam tego w kolko"
            $settledRect[$key] = $after
        }
    }

    # direwolf under Audio PTT (its title bar hides under Audio PTT's bottom edge). Only ever
    # lowered right below Audio PTT - never raised over the user's other windows.
    if ($DryRun) { return }
    if (Set-AprsStacking) { Write-WatchdogLog "okno direwolf nad Audio PTT - kladzie pod nie" }
}

# Lets `. .\aprsbox-watchdog.ps1` load the functions without running the loop (tests).
if ($MyInvocation.InvocationName -eq '.') { return }

# Single instance: a second copy (manual run next to the task) exits at once.
# -Once is a diagnostic pass and may run next to the real watchdog.
if (-not $Once) {
    $createdNew = $false
    $mutex = New-Object System.Threading.Mutex($true, 'Local\APRSBox-Watchdog', [ref]$createdNew)   # held until the process ends
    if (-not $createdNew) { exit 0 }
}

# Task Scheduler may start us BelowNormal; keep the watchdog itself at Normal so the check
# never starves under load. (The launched programs get Normal from WMI regardless.)
try { (Get-Process -Id $PID).PriorityClass = 'Normal' } catch {}

Write-WatchdogLog "aprsbox-watchdog.ps1 wystartowal (PID $PID), programy co $checkSec s, uklad okien co $layoutSec s$(if ($DryRun) { ', DryRun' })$(if ($Once) { ', Once' })"

if (-not $Once) { Start-Sleep -Seconds $startupGrace }

$serviceCheckDue = Get-Date
$heartbeatDue    = (Get-Date).AddHours(6)
while ($true) {
    try {
        if ((Get-Date) -ge $serviceCheckDue) {
            $serviceCheckDue = (Get-Date).AddSeconds($checkSec)
            Invoke-Check
        }
        Invoke-LayoutCheck
        if ((Get-Date) -ge $heartbeatDue) {
            $up = ($services | Where-Object { Test-ServiceRunning $_ } | ForEach-Object { $_.Name }) -join ', '
            Write-WatchdogLog "dziala, uslugi aktywne: $up"
            $heartbeatDue = (Get-Date).AddHours(6)
        }
    } catch {
        Write-WatchdogLog "BLAD petli: $($_.Exception.Message)"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $layoutSec
}
