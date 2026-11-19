# APRSBox dla Windows (natywne okno)

Port [SQ9MDD/APRSBox](https://github.com/SQ9MDD/APRSBox) na Windows. Zamiast panelu WWW jest zwykłe okno programu (PySide6/Qt). Cała logika APRS (interfejsy KISS, APRS-IS, digipeater/reguły routingu, IGate, beacony, obiekty, biuletyny, WX, alarmy CAWF, wiadomości, statystyki, warunki pasma) to ten sam kod usług co w oryginale. Działa w tym samym procesie co okno.

## Uruchamianie

- Kliknij dwukrotnie **`APRSBox.exe`** (plik z ikoną APRSBox). Program startuje bez okna konsoli. Używa środowiska `.venv`, a gdy go nie ma, Pythona dołączonego do wersji przenośnej (folder `python\`). `APRSBox.cmd` robi to samo. `APRSBox.exe` budujesz od nowa poleceniem `launcher\build.cmd` (kompilator C# z Windows).
- Albo ręcznie: `.venv\Scripts\pythonw.exe APRSBox.pyw [opcje]` (w wersji przenośnej: `python\pythonw.exe APRSBox.pyw [opcje]`).

Opcje:

| Opcja | Działanie |
|---|---|
| `--minimized` | start schowany w obszarze powiadomień (zasobnik) |
| `--no-runtime` | samo okno, bez uruchamiania interfejsów i nadawania (np. do przeglądania danych) |
| `--page NAZWA` | strona startowa, np. `map`, `traffic`, `messages` |

Dla jednego folderu danych może działać tylko jedna kopia programu. Druga kopia zablokowałaby ten sam port COM i nadawałaby ramki podwójnie.

## Wersja przenośna (ZIP)

Plik `AprsBOX.zip` zawiera cały program razem z Pythonem 3.13 i potrzebnymi bibliotekami (folder `python\`). Nie trzeba niczego instalować. Wymagany jest 64-bitowy Windows 10 lub 11.

1. Rozpakuj ZIP do folderu, w którym masz prawo zapisu, np. na Pulpit, do Dokumentów albo `C:\APRS\`. Nie rozpakowuj do `C:\Program Files`, bo program zapisuje dane we własnym folderze.
2. Jeśli ZIP pochodzi z internetu, przed rozpakowaniem otwórz jego **Właściwości** i zaznacz **Odblokuj**. Inaczej Windows może pytać o zgodę przy uruchamianiu.
3. Uruchom `AprsBOX\APRSBox.exe`.

ZIP nie zawiera danych stacji (`data\`, `logs\`, `config\`, `backups\`), więc program startuje z czystą konfiguracją. Ustawienia przeniesiesz przez **Ustawienia → Kopia konfiguracji** (eksport/import JSON) albo kopiując folder `data\` przy zamkniętym programie.

## Instalacja od zera

Dotyczy folderu bez dołączonego Pythona (np. świeżej kopii z repozytorium). Wersja przenośna z ZIP-a działa od razu. Potrzebny jest Python 3.13 dla Windows.

```bat
cd "C:\...\AprsBOX"
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-desktop.txt
```

Do uruchamiania testów zainstaluj zamiast tego `requirements-dev-windows.txt`.

## Gdzie są dane

Wersja jest przenośna. Obok folderu `app\` program tworzy:

- `data\aprsbox.db` – baza (konfiguracja, ruch, wiadomości, statystyki),
- `logs\`, `config\desktop.ini` (układ okna, ostatni widok mapy),
- `backups\`.

Inną lokalizację wskażesz zmienną środowiskową `APRSBOX_HOME`. Kopię samej konfiguracji zrobisz w **Ustawienia → Kopia konfiguracji** (eksport/import JSON).

## Pierwsze uruchomienie

1. **Moja stacja**: znak, SSID, pozycja, symbol, ścieżka i interwał beaconu. Nadawanie (TX) jest domyślnie wyłączone.
2. **Interfejsy**: dodaj TNC.
   - *KISS przez port szeregowy*: wpisz port, np. `COM5`, albo adres pyserial (`socket://host:port`). Listę portów odświeża przycisk obok pola.
   - *KISS TCP*: np. Direwolf, `127.0.0.1:8001`.
   - *APRS-IS*: serwer, port, login i passcode (passcode liczy się automatycznie ze znaku).
   - *OpenWebRX MQTT*: tylko odbiór.
3. Dopiero po sprawdzeniu ustawień włącz TX w **Moja stacja** i ewentualnie reguły routingu (digipeater) w **Reguły routingu**.

Port COM może mieć otwarty tylko jeden program naraz. Jeśli ten sam TNC obsługuje już inny program (np. APRSIS32), nie dodawaj go tutaj. Direwolf przez KISS TCP przyjmuje wielu klientów jednocześnie.

Jeśli równolegle działa inny klient APRS-IS (np. APRSIS32), ustaw w APRSBox inny login APRS-IS (ten sam znak z innym SSID, np. `ZNAK-10`). Dwa połączenia z identycznym loginem mogą się wzajemnie rozłączać.

Logowanie jest domyślnie wyłączone. Konto `admin` / `aprs` powstaje automatycznie, gdy nie ma żadnych użytkowników. Logowanie przy starcie włączysz w **Ustawienia → Aplikacja desktopowa**. Wcześniej zmień hasło w **Użytkownicy / Role**.

## Ścieżki RF na mapie (nowa funkcja)

Przycisk **Pokaż ścieżki RF** na pasku mapy rysuje drogę każdej usłyszanej ramki, tak jak APRSIS32: stacja → kolejne digipeatery (WIDE1, WIDE2…) → Twoja stacja. Dotyczy to wszystkich usłyszanych stacji, nie tylko digipeaterów.

- Linia pełna, zielona: ostatni skok, który Twoja stacja usłyszała bezpośrednio.
- Linia bursztynowa: skok przez digipeater.
- Linia przerywana: brakuje pozycji pośredniego digipeatera (skok jest pewny, przebieg nie).
- Linia turkusowa: ramka z APRS-IS (qAR/qAO), rysowana do IGate'a, który ją usłyszał.

Okno czasowe (np. 60 min) i filtry RF / APRS-IS wybierzesz obok przycisku. Najechanie na linię pokazuje znaki i pełne ścieżki.

## Okno, zasobnik, autostart

- Zamknięcie okna kończy program (po jednym pytaniu o potwierdzenie, bo zatrzymuje to digi, IGate i beacony). Jeśli wolisz chować okno do zasobnika, włącz to w **Ustawienia → Aplikacja desktopowa**. Z menu ikony w zasobniku pokażesz okno, wyślesz beacon albo zakończysz program.
- Tamże: start zminimalizowany, dźwięk przy oknach EMERGENCY/alarmów, **uruchamianie razem z Windows** (wpis w rejestrze użytkownika, bez uprawnień administratora).
- **Ikona programu to symbol APRS Twojej stacji** (jak w APRSIS32). Widać go na pasku zadań, w zasobniku, na pasku tytułu i w Alt+Tab. Zmienia się od razu po zmianie symbolu w **Moja stacja** i zgodnie z wybranym zestawem ikon (klasyczny/nowoczesny). Dopóki nie ustawisz znaku stacji, widać logo APRSBox. Opcję wyłączysz w **Ustawienia → Aplikacja desktopowa**.
- Wylogowanie i zamykanie Windows kończą program bez pytań, a runtime APRS zatrzymuje się poprawnie.

## Różnice względem wersji Linux/WWW

| Wersja WWW | Wersja Windows |
|---|---|
| panel w przeglądarce | natywne okno; wszystkie strony i podstrony przeniesione |
| HTTPS, certyfikaty | niepotrzebne (nie ma serwera WWW) – sekcja pominięta |
| aktualizacja przez git z panelu | podmiana folderu programu (dane zostają); „Sprawdź wersję w repozytorium” pokazuje, czy upstream ma nowszą wersję |
| „Restart usług” (systemd) | restart środowiska APRS wewnątrz okna |
| restart / wyłączenie hosta | polecenie `shutdown` Windows (10 s opóźnienia, z potwierdzeniem) |
| Avahi / adres panelu | na pulpicie: nazwa komputera, adresy IP i udostępnione porty KISS |

## Testy

```bat
.venv\Scripts\python -m pytest
```

Testy wymagają środowiska `.venv` z `requirements-dev-windows.txt`. Wersja przenośna go nie zawiera.

Testy okna działają bez ekranu (`QT_QPA_PLATFORM=offscreen`) na tymczasowej bazie z symulowanym ruchem. Test end-to-end uruchamia prawdziwy runtime z fałszywym TNC KISS i fałszywym serwerem APRS-IS na losowych portach `127.0.0.1`. W czasie testu rozwiązywanie nazw działa wyłącznie dla localhost, więc test nie połączy się z prawdziwym APRS-IS ani z żadnym innym serwerem.

Zrzuty wszystkich stron (kontrola wyglądu):

```bat
.venv\Scripts\python scripts\desktop_screenshots.py C:\Temp\shots dashboard map traffic --full
```
