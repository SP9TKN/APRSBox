. "$PSScriptRoot\WindowUtils.ps1"

# Zapisuje biezace polozenie okien stacji do layout.json - od teraz fix-layout, start-aprsbox
# i watchdog ustawiaja okna wlasnie tak. Kolejnosc: edit-layout, ulozenie okien, save-layout.
# Okna zminimalizowane, zmaksymalizowane albo niedzialajace zachowuja poprzednie miejsce.

$layout = [ordered]@{}
foreach ($name in $AprsLayout.Keys) {
    $win = Get-AprsWindow -ProcessName $name
    if (-not $win) {
        Write-Warning "$name nie dziala - zostawiam jego poprzednie miejsce."
        $layout[$name] = $AprsLayout[$name]
        continue
    }
    if ([Win32AprsBox]::IsIconic($win.Handle) -or [Win32AprsBox]::IsZoomed($win.Handle)) {
        Write-Warning "$name jest zminimalizowany albo zmaksymalizowany - zostawiam jego poprzednie miejsce."
        $layout[$name] = $AprsLayout[$name]
        continue
    }
    $r = New-Object Win32AprsBoxRect
    [void][Win32AprsBox]::GetWindowRect($win.Handle, [ref]$r)
    $layout[$name] = [ordered]@{ X = $r.Left; Y = $r.Top; W = $r.Right - $r.Left; H = $r.Bottom - $r.Top }
    Write-Host ("{0}: {1},{2} {3}x{4}" -f $name, $r.Left, $r.Top, ($r.Right - $r.Left), ($r.Bottom - $r.Top))
}
$layout | ConvertTo-Json | Set-Content -Path $AprsLayoutPath -Encoding ASCII
if (Test-Path $AprsLayoutEditFlag) { Remove-Item -Path $AprsLayoutEditFlag -Force }
Write-Host "Zapisano uklad do $AprsLayoutPath - watchdog znow pilnuje okien."
