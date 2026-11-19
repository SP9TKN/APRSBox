. "$PSScriptRoot\WindowUtils.ps1"

# Zmiana ukladu okien: watchdog przestaje przestawiac okna (najdluzej na 15 min), zeby mozna je
# bylo spokojnie ulozyc po swojemu. Potem save-layout zapisuje nowy uklad i wlacza pilnowanie.
Set-Content -Path $AprsLayoutEditFlag -Value "ukladanie okien od $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Encoding ASCII
Write-Host "Ukladanie okien wlaczone: watchdog nie przestawia okien przez $AprsLayoutEditMinutes min."
Write-Host "Uloz okna APRSBox, Audio PTT i direwolf, a potem uruchom save-layout."
