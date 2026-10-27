# Wabot 1.6 - killmail channels and test messages

## Przypisanie systemów do kanałów

Zatrzymaj bota, otwórz **Pochven PvP**, wybierz Kino, Niarja lub Archee,
wpisz ID kanału i kliknij **Dodaj / zmień**. Kilka systemów może mieć ten sam kanał.
Włącz monitorowanie, wybierz ping i kliknij **Zapisz ustawienia PvP**.
Po uruchomieniu bot sprawdza nowe killmaile w stałym cyklu co **30 sekund**.
Każdy killmail ma własną wiadomość z nazwą systemu, czasem UTC i linkiem zKillboard.
Nie ma progu strat, okna walki ani cooldownu. Kapsuły i straty od NPC także są wysyłane.

Discord może rozwinąć link do karty zKillboard z opisem i miniaturą.
Wymagane uprawnienia kanału: View Channel, Send Messages i Embed Links.
Ping `everyone` wymaga Mention Everyone; ping `role` wymaga właściwej roli i prawa do wzmianki.
Domyślnie nowe konfiguracje używają `none` — bez pingowania.

Feed zawiera tylko killmaile opublikowane w zKillboard. 30 sekund to okres sprawdzania,
nie gwarancja czasu od zniszczenia statku. Opóźnienia publikacji, awarie sieci i limity usług
mogą wydłużyć czas. Bot zachowuje kursor odczytu i zabezpieczenie przed duplikatami w SQLite.
Niepewna wysyłka po utracie odpowiedzi nie jest automatycznie ponawiana.

## Test zapisanych kanałów

Po zapisaniu ustawień kliknij **Test zapisanych kanałów** w **Pochven PvP**.
Bot wyśle wyraźnie oznaczoną wiadomość **TEST WABOT** na każdy zapisany kanał PvP.
W **Ustawienia → Połączenie i rynek** przycisk **Test zapisanych kanałów Discord**
obejmuje również zapisany kanał raportów rynku.

Wspólny kanał otrzymuje jedną wiadomość z listą przypisań. Test nie pinguję osób ani ról,
działa również podczas pracy bota i używa zapisanych ustawień, a nie niezapisanych zmian.
Wynik każdego kanału pojawia się pod przyciskiem i w logach. Błąd jednego kanału
nie blokuje testowania pozostałych. Brak potwierdzenia wysyłki wymaga sprawdzenia kanału
przed ponownym kliknięciem. Test nie zmienia timerów, statystyk ani historii killmaili.

## Aktualizacja starszej konfiguracji

Pozostałości alertów zbiorczych usunięto z GUI i programu. Stary `channel_id` jest przy
odczycie przekształcany we wspólny kanał dla Kino, Niarja i Archee, jeśli nie ma już przypisań.
Progi, cooldown oraz nieużywane szablony wiadomości zbiorczych są pomijane i usuwane
przy następnym zapisie. Istniejące przypisania system → kanał mają pierwszeństwo.

Aktualny format sekcji `pvp`:

```json
{
  "enabled": true,
  "ping": "none",
  "role_id": null,
  "system_channels": {
    "Kino": "123456789",
    "Niarja": "123456789",
    "Archee": "987654321"
  }
}
```

## Edycja wiadomości

W **Ustawienia → Wiadomości / pingi** można edytować przypomnienie po 23 minutach,
potwierdzenie `/timer`, statystyki, ranking i opisy raportów rynku.
Podgląd jest lokalny; nie wysyła wiadomości. Pola szablonów są opisane nad edytorem.
Zapisz zmiany i uruchom bota ponownie. Killmaile zachowują zwykły link zKillboard.

## Correct statistics in the GUI

Stop the bot and open **Ustawienia → Statystyki / ranking**. Select the server and refresh the data. Select a player, choose Kino, Niarja or Archee, enter the desired total completion count, then click **Zapisz korektę**. Enter `0` to reset that player's count in the selected system. **Dodaj gracza ręcznie** accepts a Discord user ID and display name for a player without previous timer records.

The correction updates both `/stats` and `/rank`. Future timer completions add to the corrected count. Players with zero total completions are excluded from the Discord leaderboard. Corrections are isolated to the selected server and system; they do not create, delete or reschedule reminders. A stale edit is rejected if another completion changed the count since the GUI read it.

**Ostatnie korekty** displays the most recent 50 changes with UTC time and before/after values. The full correction history is stored in SQLite and is included in backups and profile exports. To undo a correction, set the count back to the earlier value. The database upgrades automatically from previous versions; do not run an older application against the upgraded database.

Edit the text of `/stats` and `/rank` in the adjacent **Wiadomości / pingi** tab using the **Statystyki — /stats** and **Ranking — /rank** selections.
