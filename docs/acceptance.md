# Odbiór bota

## Testy automatyczne

Uruchom `python -m pytest -q` w środowisku z `requirements-dev.txt` i zainstalowanym pakietem. Testy obejmują:

- UTC, ścisłe HH:MM, minuty z sekundą 00, przejście przez północ i rok, granicę 23 minut.
- Prywatne błędy, uprawnienia, zwykłe kanały tekstowe, publiczne potwierdzenia bez wzmianek.
- Atomowy i idempotentny zapis, równoległe wywołania, rollback, awarię zapisu, restart.
- Oddzielne serwery, wszystkie statusy pingu, zmianę nazwy autora, ranking 12 osób i globalne remisy.
- Ping po terminie, opóźnienie 30/120/300 sekund i pominięcie po 301/360 sekundach.
- Retry, `429 retry_after`, timeout z nieznanym wynikiem, brak kanału/uprawnień, brak powtórnego pingu po restarcie.
- Pełny cykl: payout 20:20 Kino → restart → ping 20:43 → payout 20:50 Archee → ping 21:13. Statystyki końcowe: Kino 1, Archee 1, razem 2.
- Backup aktywnej bazy WAL, siedem kopii, kopię zewnętrzną, bezpieczne odtworzenie i odrzucenie nieprawidłowej kopii.

## Test na serwerze Discord — do wykonania po konfiguracji

Użyj osobnego serwera testowego: test publikuje rzeczywiste `@everyone`. Utwórz zwykły kanał tekstowy, nadaj botowi View Channel, Send Messages i Mention Everyone. Zsynchronizuj komendy.

1. O 20:25 EVE Time wywołaj `/timer time:20:20 system:Kino`. Potwierdzenie powinno wskazać payout 20:20, respawn 20:43, Timer ID i +1, bez pingu.
2. Sprawdź `/stats` i `/rank`. Zapisz sumy początkowe i potwierdź przyrost o jeden tylko dla autora i Kino.
3. Zrestartuj proces przed 20:43. O 20:43, zwykle do 10 sekund po terminie, oczekuj jednego `@everyone` z prośbą o sprawdzenie Kino, Niarja i Archee. Sam ping nie zmienia sum.
4. Po kolejnym ukończeniu w Archee z payoutem 20:50 wywołaj `/timer time:20:50 system:Archee`. Oczekuj nowego Timer ID, +1 Archee i tego autora, a o 21:13 kolejnego przypomnienia.
5. Zgłoś złą godzinę oraz przeterminowany payout: prywatny błąd, bez zmiany sum. Bez Mention Everyone przed zapisem: odrzucenie.
6. Odbierz Mention Everyone dopiero po przyjęciu następnego timera: przy terminie powinna pojawić się informacja bez pingu; w logu Timer ID i ostrzeżenie, statystyki zachowane.
7. Zatrzymaj bota i uruchom 2 minuty po terminie: `Delayed reminder`. Powtórz z 6 minutami: brak pingu, `skipped_expired` w logu, punkt nadal liczony.
8. Sprawdź izolację na drugim skonfigurowanym serwerze, zmianę nicku, brak pingu z nazw zawierających wzmianki, niedostępność w wątkach i DM.

Zamiast podanych godzin można użyć bieżących godzin UTC, zachowując odstęp 23 minut od payoutu.

## Hosting 24/7 — do wykonania na VPS

- Uruchom Compose na zewnętrznym hoście, sprawdź trwały wolumen, jedną replikę i politykę restartu.
- Wyłącz komputer właściciela, pozostaw VPS włączony. Z innego urządzenia wykonaj `/timer` i potwierdź późniejsze przypomnienie.
- Zrestartuj VPS i sprawdź zachowanie timera, danych oraz brak drugiego pracownika.
- Sprawdź log ostatniego backupu i faktyczną kopię poza VPS. Odtwórz snapshot na osobnym wolumenie testowym; stare terminy muszą być pominięte, sumy zachowane.
- Zanotuj datę, serwer, Timer ID, czas planowany i rzeczywisty oraz wynik każdego punktu.

Nie oznaczaj wdrożenia jako odebrane przed wykonaniem tych testów. Testy lokalne nie potwierdzają działania na prawdziwym Discordzie ani przy wyłączonym komputerze właściciela.
