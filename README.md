# Wabot — payout i respawn site’ów w EVE

Wersja 1.6: [killmaile co 30 sekund, przypisania system → kanał, test wiadomości na zapisanych kanałach](docs/pochven-and-messages.md). Usunięto stary tryb alertów zbiorczych i progi strat.


Bot Discord zgodny ze specyfikacją 1.1 z 18.09.2026, rozszerzony o monitoring rynku Jita i Perimeter. Python, discord.py i SQLite; jedna instancja na VPS lub hostingu stałych procesów. Interfejs bota jest po angielsku. Domena i serwer WWW nie są potrzebne. Moduł rynku korzysta z publicznego ESI, bez logowania do EVE.

## Działanie

| Komenda | Wynik |
| --- | --- |
| `/timer time:20:20 system:Kino` | Zapisuje jedno ukończenie w Kino i planuje przypomnienie na 20:43 UTC. |
| `/stats` | Liczba przyjętych zgłoszeń w Kino, Niarja i Archee oraz suma na bieżącym serwerze. |
| `/rank page:1` | Ranking autorów na bieżącym serwerze, 10 osób na stronie, miejsca przy remisach 1, 1, 3. |
| `/market-now` | Administrator publikuje pełny raport Jita + Perimeter na skonfigurowanym kanale. |
| `/market-status` | Administrator sprawdza świeżość danych, ostatni raport i termin następnego. |

Komendy rynku są rejestrowane po włączeniu modułu w konfiguracji i wykonaniu `sync`.

`time` to czas **payoutu**, w formacie HH:MM, w EVE Time (UTC). Bot wyznacza ostatnie wystąpienie tej godziny względem czasu utworzenia interakcji. O 00:02 wpis 23:50 odnosi się do poprzedniego dnia i daje termin 00:13. Zgłoszenie z terminem już osiągniętym jest odrzucane.

Jeden autor zgłasza jedno ukończenie w imieniu grupy. Nowe wywołanie komendy, nawet z identycznymi parametrami, daje kolejny punkt. Powtórzona dostawa tego samego `interaction_id` daje dokładnie jeden rekord. Zmiana nicku nie zmienia wyniku.

Po 23 minutach od payoutu bot publikuje nową wiadomość:

> @everyone — A new site is expected. Check Kino, Niarja and Archee! After completing it, submit /timer with the payout time in EVE Time (UTC) and the completion system. Timer ID: ID

Bot nie przewiduje lokalizacji nowego site’a. Kolejny timer wymaga kolejnego zgłoszenia. Potwierdzenia, błędy i rankingi nie pingują użytkowników ani ról.

## Aplikacja Discord

1. Utwórz aplikację i bota w [Discord Developer Portal](https://discord.com/developers/applications). Skopiuj token do prywatnej konfiguracji środowiska. Nie publikuj go w repozytorium ani w rozmowach.
2. Pozostaw wyłączone wszystkie **Privileged Gateway Intents**. Bot nie odczytuje zwykłych wiadomości ani pełnej listy członków.
3. W OAuth2 URL Generator wybierz `bot` i `applications.commands`. Wybierz uprawnienia **View Channel**, **Send Messages**, **Mention Everyone** oraz **Embed Links** dla raportów rynku. Wartość bitowa: `134237184`. Bot nie potrzebuje uprawnienia Administrator.
4. Zainstaluj bota na swoim serwerze. W ustawieniach Discorda włącz Developer Mode i skopiuj ID serwera do `config.json`. Dodaj wszystkie obsługiwane serwery do `guild_ids` jako ciągi znaków.
5. Sprawdź nadpisania uprawnień na kanałach: bot musi widzieć zwykły kanał tekstowy, wysyłać wiadomości i mieć Mention Everyone. DM, wątki, forum i kanały ogłoszeń są odrzucane.
6. Dostęp użytkowników do komend ograniczaj standardowymi ustawieniami integracji Discorda. Bot nie ma własnego systemu ról.

Użytkownicy mogą wyciszyć `@everyone` po swojej stronie. Bot nie omija tych ustawień.

## Rynek Jita + Perimeter

Zgodnie z późniejszą decyzją właściciela **Jita 4-4 i Perimeter tworzą jeden wspólny rynek w raporcie**. Jest to zmiana względem pierwotnej specyfikacji wymagającej osobnych embedów. Bot pobiera wszystkie strony zleceń The Forge, ale uwzględnia wyłącznie Jita 4-4 (`60003760`) oraz lokalizacje z systemu Perimeter (`30000144`) zwrócone przez publiczne ESI.

Co godzinę powstaje nowy raport na kanale **1550625871247704146**. Datacastery trafiają do osobnej wiadomości, a pozostałe przedmioty do kolejnej. Dla sześciu przedmiotów raport podaje wspólne najlepsze buy/sell, pozostały wolumen przy tych cenach i spread. Datacaster ma dodatkowo bezpieczny wolumen od **500 000 ISK włącznie**, wartość brutto, średnią i pierwszy poziom poniżej progu. Łączny wolumen obejmuje różne miejsca: raport pokazuje rozkład po lokalizacjach, a realizacja całości może wymagać transportu. Nie wykonuje sprzedaży ani nie składa zleceń.

`SAFE` oznacza dostępny wolumen powyżej progu; poniżej 5000 sztuk pojawia się `LOW DEPTH`, brak kwalifikujących buy orderów daje `STOP`. `Cached` zawiera wiek danych; po 30 minutach raport pokazuje `DATA ERROR`. Błąd jednego przedmiotu pozostawia wyniki pozostałych. Raport nie pinguje użytkowników ani ról. W razie przekroczenia limitów Discorda każda grupa jest dzielona na dodatkowe wiadomości ze wspólnym czasem aktualizacji.

Datacastery mają pasek i znacznik statusu: 🟢 `SAFE`, 🟠 `LOW DEPTH`, 🔴 `STOP`, ⚪ `DATA ERROR`. Raport pozostałych przedmiotów ma fioletowy pasek, a każda pozycja własny kolorowy znacznik. Ceny kupna oznacza 🔵, a sprzedaży 🟣. Przy błędzie danych pasek raportu staje się szary; nazwy statusów pozostają widoczne obok kolorów.

Nowa konfiguracja przykładowa ma już włączony moduł i ustawiony kanał. W istniejącym `config.json` zachowaj `guild_ids` i dodaj:

```json
"market": {
  "enabled": true,
  "channel_id": "1550625871247704146",
  "interval_minutes": 60,
  "datacaster_min_buy_price": "500000",
  "datacaster_low_depth_units": 5000
}
```

Powyżej jest fragment obiektu JSON: dodaj przecinek po istniejącym polu `guild_ids`. Pełny poprawny plik i lista sześciu przedmiotów z oficjalnymi `type_id` znajdują się w [config.example.json](config.example.json). Bez pola `market` istniejąca konfiguracja zachowuje dotychczasowe działanie. Zmienne z [.env.example](.env.example), np. `MARKET_REPORT_ENABLED` i `MARKET_CHANNEL_ID`, mają pierwszeństwo przed JSON. Nazwy przedmiotów są weryfikowane przy pierwszym odczycie z ESI; błędna nazwa/ID daje `Data unavailable`. Dodawanie pozycji nie wymaga zmiany algorytmu.

Kanał musi należeć do serwera z `guild_ids`, być zwykłym kanałem tekstowym i udostępniać botowi **View Channel**, **Send Messages**, **Embed Links**. Po zmianie konfiguracji wykonaj `sync` i zrestartuj bota. Pierwszy automatyczny raport powstanie po gotowości połączenia Discord. `/market-now` nie przesuwa harmonogramu; równoległe wywołanie podczas trwającego raportu jest pomijane.

Dane i nazwy struktur są ograniczone do publicznego ESI. Endpoint regionalny **nie obejmuje pełnego rynku struktur graczy**: udostępnia ich zlecenia z zasięgiem, ale pełne dane wymagają osobnego endpointu, logowania EVE SSO i dostępu do struktury. Raport zawiera informację o tym ograniczeniu. Zobacz [oficjalny opis rynków struktur](https://docs.esi.evetech.net/docs/scenarios/structure_markets.html). Niedostępna nazwa struktury jest wyświetlana jako `Structure <id>`. Cache ostatnich poprawnych odczytów pozostaje w pamięci procesu; po restarcie potrzebny jest nowy odczyt. Moduł respektuje [cache i ETag ESI](https://developers.eveonline.com/docs/services/esi/best-practices/), [paginację](https://developers.eveonline.com/docs/services/esi/pagination/x-pages/) i [limity żądań](https://developers.eveonline.com/docs/services/esi/rate-limiting/).

## Wdrożenie Docker na VPS

Wymagany jest Docker z Compose, trwały dysk i stałe połączenie z Discordem. Hosting nie może usypiać procesu. Wszystkie poniższe polecenia uruchamiaj w katalogu projektu **na VPS**, aby bot działał również po wyłączeniu twojego komputera.

```sh
cp config.example.json config.json
cp .env.example .env
chmod 600 .env
```

W `config.json` zastąp placeholder rzeczywistym ID serwera. W `.env` wpisz prawdziwy `DISCORD_TOKEN`. Compose wczytuje `.env`; pliki z sekretami są wyłączone z kontekstu budowania obrazu i z Git.

```sh
docker compose build
docker compose run --rm --no-deps bot check
docker compose run --rm --no-deps bot sync
docker compose up -d --scale bot=1
docker compose logs --tail=100 -f bot
```

`check` sprawdza lokalną konfigurację i bazę oraz stosuje migracje. Nie sprawdza tokena ani połączenia z Discordem. `sync` loguje się i rejestruje komendy w skonfigurowanych serwerach. Uruchamiaj je przy pierwszym wdrożeniu oraz po zmianie komend lub listy serwerów. Zwykły start i reconnect nie wykonują rejestracji.

Kontener działa bez roota. Wolumen `bot-data` przechowuje bazę, logi i kopie. `restart: unless-stopped` i jedna replika są zapisane w `compose.yaml`. Blokada plikowa dodatkowo zapobiega uruchomieniu dwóch procesów z tą samą bazą. Nie uruchamiaj drugiej kopii bota na innym wolumenie. Nie używaj `docker compose down -v`, jeśli chcesz zachować dane.

| Zmienna | Domyślnie lokalnie | Znaczenie |
| --- | --- | --- |
| `DISCORD_TOKEN` | wymagany dla `run` i `sync` | Sekret bota. |
| `DATABASE_PATH` | `data/bot.sqlite3` | Plik na trwałym dysku; w Compose `/data/bot.sqlite3`. |
| `CONFIG_PATH` | `config.json` | Lista `guild_ids`; w Compose `/app/config.json`. |
| `LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR lub CRITICAL. |
| `BACKUP_DIR` | `backups` obok bazy | W Compose `/data/backups`. |
| `OFFSITE_BACKUP_DIR` | brak | Zamontowany katalog kopii poza maszyną. |

Zmiany konfiguracji obowiązują po restarcie. Usunięcie serwera z konfiguracji wyłącza nowe komendy i wysyłkę jego oczekujących przypomnień. Aby usunąć stare zarejestrowane komendy z takiego serwera, usuń integrację bota w ustawieniach tego serwera.

## Lokalne uruchomienie i testy

Dla Windows dostępny jest instalator **[WabotSetup.exe](dist/windows/WabotSetup.exe)** z launcherem, Pythonem oraz obecną konfiguracją i bazą. Obsługa Start/Stop, logi i przenoszenie danych są dostępne w oknie programu. [Instrukcja instalacji i przenoszenia](docs/windows-launcher.md).

Obsługiwany Python 3.11 lub 3.12; obraz Docker używa 3.12. Zależności, również przechodnie, są przypięte w `requirements.txt` i `requirements-dev.txt`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
Copy-Item config.example.json config.json
# Edytuj config.json. Ustaw DISCORD_TOKEN prywatnie w środowisku procesu.
# Opcjonalnie ustaw DATABASE_PATH i CONFIG_PATH dla swojego komputera.
.\.venv\Scripts\python.exe -m wabot check
.\.venv\Scripts\python.exe -m wabot sync
.\.venv\Scripts\python.exe -m wabot run
```

Nie trzeba aktywować venv ani zmieniać ExecutionPolicy PowerShell. Uruchomienie lokalne nie wczytuje automatycznie `.env`. Użyj zmiennych środowiskowych albo Compose. Nie ustawiaj lokalnie linuksowych ścieżek `/app` i `/data` z przykładu Docker.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\ruff.exe format --check src tests
```

Testy używają kontrolowanego zegara, atrap Discorda i prawdziwej tymczasowej bazy SQLite. Nie wymagają tokena i nie wysyłają wiadomości. Procedura odbioru na Discordzie i hostingu: [docs/acceptance.md](docs/acceptance.md).

## Kolejka, błędy i restart

Co 5 sekund jeden pracownik odczytuje z SQLite gotowe wpisy `pending` i `retry`. Atomowe przejście do `sending` poprzedza żądanie. Sukces zapisuje `sent`, czas i ID wiadomości. Przypomnienia wysyłane są przez REST jako nowe wiadomości bota; nie korzystają z wygasającego tokena interakcji.

- Opóźnienie 30–300 sekund: wiadomość z `Delayed reminder`. Ponad 300 sekund: `skipped_expired`, bez pingu.
- Znany błąd przejściowy: retry po 5, 15, 30, potem 60 sekundach, wyłącznie w oknie pięciu minut. `429` respektuje `retry_after`.
- Brak kanału lub trwałe `403`: `failed`. Utrata Mention Everyone po zapisie: zwykła informacja bez wzmianki i ostrzeżenie w bazie oraz logu.
- Timeout po rozpoczęciu żądania i inne niejednoznaczne wyniki: `delivery_unknown`, bez automatycznej ponownej wysyłki. Tak samo traktowane są zastane przy starcie rekordy `sending`.

Dokładnie jeden zapis zgłoszenia nie gwarantuje dokładnie jednej dostawy przez sieć. W niejednoznacznej sytuacji bot unika kolejnego pingu. Statystyki obejmują wszystkie zaakceptowane wpisy, także `failed`, `delivery_unknown` i pominięte przypomnienia. Otrzymany Timer ID pozwala odnaleźć wynik w logach.

`docker compose restart bot` restartuje proces bez utraty danych. `docker compose stop bot` kończy bieżącą dostawę i zamyka połączenia. Logi są rotowane: pięć archiwów po 5 MB w `/data/logs`, a logi kontenera mają własną rotację.

## Backup i odtworzenie

Bot wykonuje spójny snapshot przez **SQLite Backup API** po starcie i co 24 godziny, zachowując siedem najnowszych kopii. Błąd backupu powoduje zapis błędu i ponowienie po godzinie. Nie kopiuj samego aktywnego pliku `.sqlite3` przy włączonym WAL.

Ręczna kopia może działać przy uruchomionym bocie:

```sh
docker compose run --rm --no-deps bot backup
```

Kopie na tym samym VPS nie chronią przed utratą maszyny. Zamontuj zewnętrzny katalog albo regularnie przenoś gotowe snapshoty do niezależnego magazynu. Aby użyć wbudowanego kopiowania, utwórz `compose.override.yaml`:

```yaml
services:
  bot:
    environment:
      OFFSITE_BACKUP_DIR: /offsite
    volumes:
      - /mnt/independent-backup/wabot:/offsite
```

`/mnt/independent-backup/wabot` musi być faktycznie podłączonym magazynem poza VPS, zapisywalnym dla UID/GID 10001. Zwykły lokalny katalog nie spełnia wymagania kopii poza maszyną. Sprawdź montowanie i dostępność w monitoringu hostingu. Bot kopiuje gotowy, zweryfikowany snapshot i zachowuje co najmniej siedem ostatnich plików także tam.

Odtwarzanie wymaga zatrzymania bota. Wybierz plik z `/data/backups` lub wcześniej umieść zewnętrzny snapshot w tym wolumenie:

```sh
docker compose stop bot
docker compose run --rm --no-deps bot restore /data/backups/NAZWA_KOPII.sqlite3
docker compose up -d --scale bot=1
```

`restore` sprawdza integralność i wersję bazy na pliku tymczasowym. **Przed uruchomieniem kolejki** oznacza wszystkie rekordy z terminem nie późniejszym niż chwila odtworzenia jako `skipped_expired`. Chroni to przed ponownymi pingami ze starej kopii; statystyki pozostają. Nie zastępuj tego polecenia ręcznym kopiowaniem pliku na aktywną bazę. Blokady wykluczają jednoczesne odtwarzanie, pracę bota i ręczny backup.

## Aktualizacja

1. Zrób backup poleceniem powyżej i sprawdź kopię poza maszyną.
2. `docker compose stop bot`, następnie wgraj nowe źródła i wykonaj `docker compose build`.
3. `docker compose run --rm --no-deps bot check` zastosuje wersjonowane migracje z `src/wabot/migrations`.
4. Jeśli zmieniły się komendy lub serwery, wykonaj `docker compose run --rm --no-deps bot sync`.
5. `docker compose up -d --scale bot=1`, sprawdź logi i `/stats`.

Migracje są transakcyjne i nie usuwają istniejących zgłoszeń. Aplikacja odrzuca bazę o nowszej wersji schematu. Przy cofnięciu wersji programu użyj zgodnego snapshotu i procedury `restore`.

## Zakres przekazania

Źródła, migracje, konfiguracja przykładowa, Docker, testy, backup i instrukcje są w tym katalogu. Do uruchomienia produkcyjnego właściciel musi dostarczyć aplikację Discord, ID serwera, token i hosting. Testy z prawdziwym Discordem oraz potwierdzenie działania przy wyłączonym komputerze właściciela wymagają tego środowiska. Koszt przygotowania/wdrożenia i miesięczny koszt VPS to osobne pozycje; projekt nie narzuca dostawcy ani abonamentu bazodanowego.

Źródło wymagań: [wyciąg z dokumentu](docs/specification.txt). Dokumentacja: [interakcje i ich czas życia](https://docs.discord.com/developers/interactions/receiving-and-responding), [wiadomości i allowed_mentions](https://docs.discord.com/developers/resources/message), [uprawnienia](https://docs.discord.com/developers/topics/permissions).
