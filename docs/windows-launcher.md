# Wabot — instalator i launcher Windows

Gotowy instalator: `dist/windows/WabotSetup.exe`. Przenieś ten plik na drugi komputer z Windows 10/11 x64 i uruchom. Python i wszystkie biblioteki są dołączone; instalacja nie pobiera zależności i nie wymaga administratora. Do pracy bota potrzebne jest połączenie z Internetem.

Instalator tej kopii zawiera obecny `config.json` oraz spójną kopię bazy z chwili budowania. Zachowuje ID serwera, kanał rynku, raporty co 60 minut, osobne kolorowe wiadomości Datacasterów i pozostałych przedmiotów. Przy ponownej instalacji pozostawia istniejące ustawienia, bazę i token. Odinstalowanie usuwa program i skróty, ale zachowuje dane w `%LOCALAPPDATA%\Wabot`.

## Pierwsze uruchomienie

1. Otwórz **Wabot Launcher** ze skrótu na pulpicie lub z menu Start.
2. Obecna kopia źródłowa nie ma tokena Discorda. Tylko za pierwszym razem wklej token w zakładce **Ustawienia** i kliknij **Zapisz ustawienia**. Pozostałe pola są już uzupełnione. Token otrzymasz w [Discord Developer Portal](https://discord.com/developers/applications), w zakładce Bot odpowiedniej aplikacji; ID aplikacji nie jest tokenem.
3. Kliknij **Start bota**. Launcher automatycznie rejestruje komendy przy pierwszym starcie lub zmianie konfiguracji, a potem uruchamia bota. Stan „Bot działa” oznacza połączenie z Discordem.

Przycisk **Zatrzymaj** i zamknięcie launchera kończą pracę bota po obsłużeniu bieżących zadań. Bot działa, kiedy launcher i komputer są uruchomione. **Sprawdź instalację** sprawdza konfigurację i bazę bez połączenia z Discordem. Logi są widoczne w oknie oraz w folderze danych `data/logs`.

## Wyszukiwanie i monitorowanie przedmiotów EVE

1. Zatrzymaj bota i otwórz zakładkę **Przedmioty EVE**.
2. Wpisz angielską nazwę, fragment nazwy lub ID przedmiotu. Wyszukiwanie obejmuje cały katalog; przy ponad 200 wynikach doprecyzuj nazwę.
3. Zaznacz wynik i kliknij **Dodaj do monitorowanych →** (lub kliknij go dwukrotnie). Zapis następuje od razu. Przycisk **Usuń z monitorowanych** usuwa zaznaczony przedmiot z raportów.
4. Uruchom bota. Nowe przedmioty są analizowane i uwzględniane w raportach według obecnych ustawień Jita + Perimeter, kanału i częstotliwości. Dotychczasowe progi cenowe pozostają zachowane. Raporty rynku muszą być włączone w **Ustawieniach**.

Katalog pochodzi z [oficjalnego Static Data Export CCP](https://developers.eveonline.com/docs/services/static-data/) i obejmuje wszystkie opublikowane typy przedmiotów, także te bez rynku. Tylko przedmioty dostępne na rynku można dodać do analizy zleceń. Brak zleceń w Jita/Perimeter będzie widoczny w raporcie jak dotychczas.

Kopia katalogu jest dołączona do programu, więc wyszukiwanie działa offline od pierwszego uruchomienia. **Aktualizuj katalog** pobiera bieżące dane CCP w tle (około 100 MB) i zapisuje mały katalog w folderze danych użytkownika. W razie problemu z pobieraniem poprzedni katalog pozostaje dostępny. Wybrane przedmioty są przechowywane w konfiguracji i przenoszone razem z eksportem danych.

## Późniejsze przenoszenie z zachowaniem nowych danych

1. Zatrzymaj bota na starym komputerze.
2. W zakładce **Przenieś bota** kliknij **Eksportuj dane i ustawienia**.
3. Przenieś instalator i zapisany plik `Wabot-dane.wabot.zip` na drugi komputer.
4. Zainstaluj program, wybierz **Importuj pakiet danych**, a potem **Start bota**. Nie trzeba przepisywać ustawień ani zapisanego tokena.

Pakiet eksportu zawiera bazę, konfigurację i token, jeżeli został zapisany. Jest prywatną kopią bota; nie udostępniaj go innym osobom. Token w zainstalowanym launcherze jest chroniony przez Windows DPAPI dla danego użytkownika. Import zabezpiecza go ponownie dla użytkownika nowego komputera. Nie kopiuj samego `token.dpapi` pomiędzy komputerami.

Możesz też wybrać **Importuj folder starego bota**, wskazując folder z `config.json`, `data/bot.sqlite3` i opcjonalnym prywatnym `.env`. Przed zastąpieniem istniejącego profilu launcher zachowuje pakiet `backups/before-import-*.wabot.zip`. Przeterminowane przypomnienia są pomijane podczas importu, a statystyki pozostają. Uruchamiaj tylko jedną kopię bota na jednym komputerze.

## Odtworzenie instalatora po zmianach w kodzie

Na komputerze programisty z Pythonem 3.11/3.12 i [Inno Setup 6](https://jrsoftware.org/isdl.php):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts/build_windows.py --iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

Skrypt pobiera ustawienia z `config.json` i bazę z `data/bot.sqlite3`. Token bierze wyłącznie z prywatnego `.env`, zmiennej `DISCORD_TOKEN` lub wskazanego `--token-file` pliku `.env`. Nigdy nie odczytuje `.env.example`. Jeżeli dołączysz token, sam instalator również stanie się prywatnym pakietem. Pliki pakietu i instalatora pozostają w ignorowanych katalogach `build` i `dist`.

Wynikiem jest `WabotSetup.exe`, suma SHA-256, plik `build-info.json` bez sekretów i ta instrukcja. Instalator nie jest podpisany komercyjnym certyfikatem wydawcy.

Technologia pakowania: [PyInstaller](https://pyinstaller.org/en/stable/spec-files.html), [instalacja Inno Setup dla bieżącego użytkownika](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm), [ochrona tokena Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).

## Pochven PvP, wiadomości i statystyki

W zakładce **Pochven PvP** wpisz ID kanału i włącz alerty. W **Ustawienia → Wiadomości / pingi** edytujesz treść pingu timera, potwierdzenia /timer i pozostałych raportów. W **Ustawienia → Statystyki / ranking** poprawiasz wyniki graczy i przeglądasz historię korekt. Zapisz zmiany przy zatrzymanym bocie, a następnie uruchom go ponownie. [Opis ustawień i zmiennych](pochven-and-messages.md).
