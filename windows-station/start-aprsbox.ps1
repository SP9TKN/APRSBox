. "$PSScriptRoot\WindowUtils.ps1"   # sciezki programow i uklad okien: $AprsExe, $AprsLayout

# Uruchamia program i ustawia jego okno. Jesli ten program juz dziala - np. podniosl go watchdog -
# nie startuje drugiej kopii, tylko bierze istniejace okno.
function Start-AprsApp {
    param(
        [Parameter(Mandatory = $true)][string]$ProcessName,
        [int]$TimeoutSec = 30
    )
    $exe = $AprsExe[$ProcessName]
    if (-not (Get-AprsProcess -ProcessName $ProcessName)) {
        $arguments = $AprsArgs[$ProcessName]
        if ($arguments) {
            Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -ArgumentList $arguments
        } else {
            Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe)
        }
    } else {
        Write-Host "  $ProcessName juz dziala - nie uruchamiam drugiej kopii."
    }
    $win = Wait-MainWindow -ProcessName $ProcessName -TimeoutSec $TimeoutSec
    $pos = $AprsLayout[$ProcessName]
    Set-WindowPosition -Handle $win.Handle -X $pos.X -Y $pos.Y -W $pos.W -H $pos.H
    $win
}

# Watchdog znow moze pilnowac stacji (stop-aprsbox wstrzymal go plikiem-flaga).
if (Test-Path $AprsStopFlag) { Remove-Item -Path $AprsStopFlag -Force }

Write-Host "Uruchamiam direwolf..."
$direwolf = Start-AprsApp -ProcessName 'direwolf'

# Audio PTT po direwolfie: lampki PTT czyta z COM4, ktory trzyma direwolf.
Write-Host "Uruchamiam Audio PTT..."
$audioPtt = Start-AprsApp -ProcessName 'AudioPTT'

# APRSBox na koncu: laczy sie z KISS direwolfa (127.0.0.1:8100) i z LoRa (COM3); startuje kilka sekund.
Write-Host "Uruchamiam APRSBox..."
$aprsbox = Start-AprsApp -ProcessName 'APRSBox' -TimeoutSec 60

Write-Host "Ustawiam kolejnosc okien (APRSBox na wierzchu, direwolf pod Audio PTT)..."
Set-AprsBottomOrder
Start-Sleep -Milliseconds 200
Move-WindowToTop -ProcessId $aprsbox.Process.Id

Write-Host "Gotowe - stacja APRSBox uruchomiona."
