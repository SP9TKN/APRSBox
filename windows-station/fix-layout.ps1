. "$PSScriptRoot\WindowUtils.ps1"   # uklad okien: $AprsLayout

# Ten sam uklad okien co w start-aprsbox.ps1 - gdy monitor zgubil sygnal (wylaczenie, KVM, sleep)
# i Windows porozrzucal okna po powrocie. W przeciwienstwie do start-aprsbox.ps1 NIC tu nie jest
# uruchamiane ani restartowane - tylko ustawiane juz dzialajace okna, wiec stacja dziala bez przerwy.

foreach ($procName in $AprsLayout.Keys) {
    $win = Get-AprsWindow -ProcessName $procName
    if (-not $win) {
        Write-Warning "$procName nie dziala (albo nie ma jeszcze glownego okna) - pomijam."
        continue
    }
    $pos = $AprsLayout[$procName]
    Set-WindowPosition -Handle $win.Handle -X $pos.X -Y $pos.Y -W $pos.W -H $pos.H
    Write-Host "$procName -> ustawione na miejsce."
}

# Okna sa ustawiane asynchronicznie - chwila, zeby po powrocie z tego skryptu staly juz na miejscu.
Start-Sleep -Milliseconds 500
if (Set-AprsStacking) { Write-Host "direwolf -> pod Audio PTT." }
Write-Host "Gotowe - uklad okien przywrocony."
