. "$PSScriptRoot\WindowUtils.ps1"

# Najpierw flaga dla watchdoga: stacja zatrzymana celowo, nie podnosic jej az do start-aprsbox.
Set-Content -Path $AprsStopFlag -Value "zatrzymane przez stop-aprsbox $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Encoding ASCII

# APRSBox pierwszy: konczy digi, iGate i beacony, rozlacza KISS i LoRa (COM3).
Write-Host "Zamykam APRSBox..."
Stop-AprsBox

Write-Host "Zamykam Audio PTT..."
Stop-AppGracefully -ProcessName 'AudioPTT'

Write-Host "Zamykam direwolf..."
Stop-AppGracefully -ProcessName 'direwolf'

Write-Host "Gotowe - stacja zatrzymana. Watchdog (jesli dziala) nie podniesie jej az do start-aprsbox."
