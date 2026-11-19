Add-Type @"
using System;
using System.Runtime.InteropServices;
public struct Win32AprsBoxRect { public int Left, Top, Right, Bottom; }
public class Win32AprsBox {
    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out Win32AprsBoxRect rect);
    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool IsZoomed(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam, uint flags, uint timeout, out IntPtr result);

    delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr hWnd, uint cmd);
    [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr hWnd, int index);

    // Glowne okno procesu: pierwsze od gory widoczne okno bez wlasciciela, z paskiem tytulu
    // (okna dialogowe APRSBox maja wlasciciela, wiec sie nie licza).
    public static IntPtr FindMainWindow(int processId) {
        const int GWL_STYLE = -16;
        const int WS_CAPTION = 0x00C00000;
        const uint GW_OWNER = 4;
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate (IntPtr h, IntPtr l) {
            uint pid;
            GetWindowThreadProcessId(h, out pid);
            if (pid != (uint)processId || !IsWindowVisible(h) || GetWindow(h, GW_OWNER) != IntPtr.Zero) return true;
            if ((GetWindowLong(h, GWL_STYLE) & WS_CAPTION) != WS_CAPTION) return true;
            found = h;
            return false;
        }, IntPtr.Zero);
        return found;
    }

    // Czy okno a lezy w Z-order nad oknem b (idzie od a w dol stosu, az trafi na b).
    public static bool IsAbove(IntPtr a, IntPtr b) {
        const uint GW_HWNDNEXT = 2;
        int guard = 0;
        for (IntPtr h = GetWindow(a, GW_HWNDNEXT); h != IntPtr.Zero && guard < 100000; h = GetWindow(h, GW_HWNDNEXT), guard++)
            if (h == b) return true;
        return false;
    }

    public static bool IsTopMost(IntPtr hWnd) {
        const int GWL_EXSTYLE = -20;
        const int WS_EX_TOPMOST = 0x8;
        return (GetWindowLong(hWnd, GWL_EXSTYLE) & WS_EX_TOPMOST) != 0;
    }
}
"@ -ErrorAction SilentlyContinue

$script:SW_RESTORE = 9
# SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_ASYNCWINDOWPOS - bez aktywowania okna
# i bez czekania na program (zawieszony program nie zablokuje watchdoga).
$script:SWP_PLACE_QUIETLY = 0x4054

# Skrypty leza w "AprsBOX\windows-station", programy stacji w folderze AprsBOX obok.
# Sciezki sa liczone od polozenia skryptow, wiec przeniesienie calego AprsBOX ich nie psuje.
$script:AprsRoot = Split-Path $PSScriptRoot -Parent

# Programy stacji APRSBox (od 2026-10-08), klucz = nazwa procesu: "glupi" direwolf 1.8.1 (tylko
# modem KISS 8100 z PTT na COM4), Audio PTT (wodospad audio + lampki PTT czytane z COM4 direwolfa)
# i APRSBox (cala logika stacji: digi, iGate, beacon, mapa). APRSBox.exe to tylko starter -
# sam program to pythonw.exe z APRSBox.pyw, patrz Get-AprsProcess.
$script:AprsExe = @{
    direwolf = Join-Path $script:AprsRoot 'direwolf-1.8.1-aprsbox290r2\direwolf-1.8.1-a231971_x86_64\direwolf.exe'
    AudioPTT = Join-Path $script:AprsRoot 'Audio PTT\AudioPTT.exe'
    APRSBox  = Join-Path $script:AprsRoot 'APRSBox.exe'
}
$script:AprsBoxScript = Join-Path $script:AprsRoot 'APRSBox.pyw'
# Argumenty startowe: APRSBox od razu na mapie.
$script:AprsArgs = @{ APRSBox = @('--page', 'map') }

# Plik, ktory stop-aprsbox zostawia, a start-aprsbox usuwa: dopoki jest, watchdog niczego nie
# uruchamia (inaczej podnioslby recznie zatrzymana stacje w ciagu 30 s).
$script:AprsStopFlag = Join-Path $PSScriptRoot 'aprsbox-stopped.flag'
# Plik, ktory zostawia edit-layout, a usuwa save-layout: dopoki jest (najdluzej 15 min), watchdog
# nie przestawia okien, wiec mozna je spokojnie ulozyc po swojemu.
$script:AprsLayoutEditFlag = Join-Path $PSScriptRoot 'layout-edit.flag'
$script:AprsLayoutEditMinutes = 15

# Uklad okien (X, Y, W, H) na pulpicie 1920x1080 - jedno miejsce dla start-aprsbox, fix-layout
# i watchdoga. To prostokaty razem z niewidocznymi ramkami Windows 10 (7 px z lewej, z prawej
# i od dolu), stad ujemne X. Zmierzone 2026-10-08 z ukladu ustawionego przez uzytkownika:
# po lewej kolumna 349 px - Audio PTT na gorze, pod nim konsola direwolfa do dolu ekranu; pasek
# tytulu direwolfa chowa sie pod dolna krawedzia Audio PTT (y 425), wiec direwolf musi lezec
# pod nim (pilnuje tego watchdog). Po prawej APRSBox na cala wysokosc.
# save-layout.ps1 zapisuje biezacy uklad do layout.json - wtedy obowiazuje on zamiast tego.
$script:AprsLayout = [ordered]@{
    APRSBox  = @{ X = 340; Y = 0;   W = 1586; H = 1047 }
    AudioPTT = @{ X = -8;  Y = 0;   W = 363;  H = 432 }
    direwolf = @{ X = -8;  Y = 392; W = 363;  H = 655 }
}
$script:AprsLayoutPath = Join-Path $PSScriptRoot 'layout.json'

# Wczytuje uklad zapisany przez save-layout (layout.json), jesli jest. Watchdog wola to ponownie,
# gdy plik sie zmieni, wiec nowy uklad obowiazuje bez restartu watchdoga.
function Import-AprsLayout {
    if (-not (Test-Path $script:AprsLayoutPath)) { return }
    try {
        $saved = Get-Content -Raw -Path $script:AprsLayoutPath | ConvertFrom-Json
        foreach ($name in @($script:AprsLayout.Keys)) {
            $rect = $saved.$name
            if ($rect) { $script:AprsLayout[$name] = @{ X = [int]$rect.X; Y = [int]$rect.Y; W = [int]$rect.W; H = [int]$rect.H } }
        }
    } catch {
        Write-Warning "Nie udalo sie odczytac $($script:AprsLayoutPath) - zostaje poprzedni uklad: $($_.Exception.Message)"
    }
}
Import-AprsLayout

# Czy uzytkownik wlasnie uklada okna (edit-layout bez save-layout, nie dluzej niz 15 min).
function Test-LayoutEditing {
    if (-not (Test-Path $script:AprsLayoutEditFlag)) { return $false }
    ((Get-Date) - (Get-Item $script:AprsLayoutEditFlag).LastWriteTime).TotalMinutes -lt $script:AprsLayoutEditMinutes
}

# APRSBox dziala jako pythonw.exe z APRSBox.pyw (z .venv sa dwa procesy: starter srodowiska
# i wlasciwy interpreter). Rozpoznajemy go po sciezce skryptu w linii polecen - na tym komputerze
# Pythona uzywa tez cwop-dashboard. Kopie z --no-runtime (podglad bez stacji) sie nie licza.
# Identyfikatory sa zapamietywane, zeby watchdog nie pytal WMI co 5 s.
$script:AprsBoxIds = @()
function Get-AprsBoxProcess {
    $alive = @($script:AprsBoxIds | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue } |
        Where-Object { $_.ProcessName -like 'python*' })
    if ($alive.Count -and $alive.Count -eq $script:AprsBoxIds.Count) { return $alive }
    $pattern = "*$($script:AprsBoxScript)*"
    $script:AprsBoxIds = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like $pattern -and $_.CommandLine -notlike '*--no-runtime*' } |
        ForEach-Object { [int]$_.ProcessId })
    if (-not $script:AprsBoxIds.Count) { return }
    Get-Process -Id $script:AprsBoxIds -ErrorAction SilentlyContinue
}

# Procesy stacji rozpoznawane po sciezce exe, nie tylko po nazwie: na tym komputerze sa tez
# direwolfy starych stacji (SDR, 290R2), ktorych te skrypty nie moga przesuwac ani zamykac.
function Get-AprsProcess {
    param([Parameter(Mandatory = $true)][string]$ProcessName)
    if ($ProcessName -eq 'APRSBox') { return Get-AprsBoxProcess }
    $exe = $script:AprsExe[$ProcessName]
    Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Where-Object { -not $exe -or $_.Path -eq $exe }
}

# Glowne okno programu stacji: obiekt z polami Process i Handle, albo nic, gdy program nie dziala
# albo nie ma jeszcze okna.
function Get-AprsWindow {
    param([Parameter(Mandatory = $true)][string]$ProcessName)
    foreach ($p in Get-AprsProcess -ProcessName $ProcessName) {
        $h = [Win32AprsBox]::FindMainWindow($p.Id)
        if ($h -ne [IntPtr]::Zero) { return [pscustomobject]@{ Process = $p; Handle = $h } }
    }
}

# Prostokat okna jako tekst "X,Y WxH" - do porownan z $AprsLayout i do logu. $null, gdy okna juz nie ma.
function Get-WindowRectText {
    param([Parameter(Mandatory = $true)][IntPtr]$Handle)
    $r = New-Object Win32AprsBoxRect
    if (-not [Win32AprsBox]::GetWindowRect($Handle, [ref]$r)) { return $null }
    "{0},{1} {2}x{3}" -f $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top)
}

function Wait-MainWindow {
    param(
        [Parameter(Mandatory = $true)][string]$ProcessName,
        [int]$TimeoutSec = 30
    )
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        $win = Get-AprsWindow -ProcessName $ProcessName
        if ($win) { return $win }
        Start-Sleep -Milliseconds 300
    }
    throw "Okno procesu '$ProcessName' nie pojawilo sie w ciagu $TimeoutSec s."
}

# Ustawia okno bez aktywowania go i bez zmiany kolejnosci okien: watchdog robi to w tle, wiec
# okno nie moze wskoczyc nad to, w czym akurat pracujesz. SW_RESTORE (ktore aktywuje) tylko
# dla okna zminimalizowanego albo zmaksymalizowanego - takiemu SetWindowPos nie zmieni rozmiaru.
function Set-WindowPosition {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$Handle,
        [Parameter(Mandatory = $true)][int]$X,
        [Parameter(Mandatory = $true)][int]$Y,
        [Parameter(Mandatory = $true)][int]$W,
        [Parameter(Mandatory = $true)][int]$H
    )
    if ([Win32AprsBox]::IsIconic($Handle) -or [Win32AprsBox]::IsZoomed($Handle)) {
        [Win32AprsBox]::ShowWindowAsync($Handle, $script:SW_RESTORE) | Out-Null
        Start-Sleep -Milliseconds 200
    }
    [Win32AprsBox]::SetWindowPos($Handle, [IntPtr]::Zero, $X, $Y, $W, $H, $script:SWP_PLACE_QUIETLY) | Out-Null
}

# Kladzie okno w Z-order tuz pod innym oknem - tylko w dol, wiec Windows tego nie blokuje i nic
# nie wyskakuje nad to, w czym akurat pracujesz. Bez aktywowania, bez czekania na program.
function Move-WindowBelow {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$Handle,
        [Parameter(Mandatory = $true)][IntPtr]$Below
    )
    $SWP_NOMOVE_NOSIZE_NOACTIVATE_ASYNC = 0x4013  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
    [Win32AprsBox]::SetWindowPos($Handle, $Below, 0, 0, 0, 0, $SWP_NOMOVE_NOSIZE_NOACTIVATE_ASYNC) | Out-Null
}

# Wysyla okno na sam spod stosu Z-order. W przeciwienstwie do HWND_TOP, przesuniecie
# na spod nie jest blokowane przez ochrone Windows przed "kradzieza" fokusu.
function Send-WindowToBottom {
    param([Parameter(Mandatory = $true)][IntPtr]$Handle)
    $HWND_BOTTOM = [IntPtr]1
    $SWP_NOMOVE_NOSIZE_NOACTIVATE = 0x0013  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
    [Win32AprsBox]::SetWindowPos($Handle, $HWND_BOTTOM, 0, 0, 0, 0, $SWP_NOMOVE_NOSIZE_NOACTIVATE) | Out-Null
}

# Przenosi okno na sam wierzch stosu Z-order. SetWindowPos(HWND_TOP) z SWP_NOACTIVATE jest
# po cichu ignorowane przez Windows, gdy cel wypadlby nad biezacym oknem pierwszoplanowym
# (ochrona przed kradzieza fokusu) - AppActivate aktywuje okno i realnie to omija.
function Move-WindowToTop {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction SilentlyContinue
    try {
        [Microsoft.VisualBasic.Interaction]::AppActivate($ProcessId)
    } catch {
        Write-Warning "Nie udalo sie aktywowac okna procesu (PID $ProcessId): $($_.Exception.Message)"
    }
}

# Kolejnosc okien stacji po starcie: direwolf pod Audio PTT (jego pasek tytulu chowa sie pod dolna
# krawedzia Audio PTT). Oba wysylane na spod - bez kradziezy fokusu.
function Set-AprsBottomOrder {
    $ptt = Get-AprsWindow -ProcessName 'AudioPTT'
    $dw  = Get-AprsWindow -ProcessName 'direwolf'
    if ($ptt) { Send-WindowToBottom -Handle $ptt.Handle; Start-Sleep -Milliseconds 200 }
    if ($dw)  { Send-WindowToBottom -Handle $dw.Handle }
}

# Pilnuje tylko tego jednego warunku: direwolf lezy pod Audio PTT. Tylko w dol, tuz pod Audio PTT -
# nic nie wyskakuje nad okna, w ktorych pracujesz. Zwraca $true, gdy cos poprawil.
# Pomija Audio PTT ustawione "zawsze na wierzchu" (menu Audio PTT) - wtedy i tak jest nad direwolfem.
function Set-AprsStacking {
    $ptt = Get-AprsWindow -ProcessName 'AudioPTT'
    $dw  = Get-AprsWindow -ProcessName 'direwolf'
    if (-not $ptt -or -not $dw) { return $false }
    if ([Win32AprsBox]::IsIconic($ptt.Handle) -or [Win32AprsBox]::IsIconic($dw.Handle)) { return $false }
    if ([Win32AprsBox]::IsTopMost($ptt.Handle) -or [Win32AprsBox]::IsTopMost($dw.Handle)) { return $false }
    if (-not [Win32AprsBox]::IsAbove($dw.Handle, $ptt.Handle)) { return $false }
    Move-WindowBelow -Handle $dw.Handle -Below $ptt.Handle
    $true
}

function Stop-AppGracefully {
    param(
        [Parameter(Mandatory = $true)][string]$ProcessName,
        [int]$TimeoutSec = 8
    )
    $procs = Get-AprsProcess -ProcessName $ProcessName
    if (-not $procs) { return }

    foreach ($p in $procs) { $p.CloseMainWindow() | Out-Null }

    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if (-not (Get-AprsProcess -ProcessName $ProcessName)) { return }
        Start-Sleep -Milliseconds 300
    }

    Get-AprsProcess -ProcessName $ProcessName | Stop-Process -Force
}

# APRSBox zamykany jak przy wylogowaniu z Windows (WM_QUERYENDSESSION + WM_ENDSESSION): bez pytania
# "Zakonczyc?", ktore pokazuje zwykle zamkniecie okna, a stacja (digi, iGate, polaczenia) zatrzymuje
# sie porzadnie i APRSBox zapamietuje uklad okna. Dopiero gdy to nie zadziala - zabicie procesu.
function Stop-AprsBox {
    param([int]$TimeoutSec = 30)
    if (-not (Get-AprsProcess -ProcessName 'APRSBox')) { return }
    $win = Get-AprsWindow -ProcessName 'APRSBox'
    if ($win) {
        $result = [IntPtr]::Zero
        $logoff = [IntPtr]([Int64]0x80000000)   # ENDSESSION_LOGOFF
        [Win32AprsBox]::SendMessageTimeout($win.Handle, 0x0011, [IntPtr]::Zero, $logoff, 0x2, 5000, [ref]$result) | Out-Null
        [Win32AprsBox]::SendMessageTimeout($win.Handle, 0x0016, [IntPtr]1, $logoff, 0x2, 5000, [ref]$result) | Out-Null
    }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if (-not (Get-AprsProcess -ProcessName 'APRSBox')) { return }
        Start-Sleep -Milliseconds 300
    }
    Write-Warning "APRSBox nie zamknal sie w ciagu $TimeoutSec s - zamykam na sile."
    Get-AprsProcess -ProcessName 'APRSBox' | Stop-Process -Force
}
