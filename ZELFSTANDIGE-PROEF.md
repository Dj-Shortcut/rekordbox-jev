# Zelf controleren zonder Codex

1. Open Rekordbox in de bestaande indeling **2Deck Horizontal**, met map **26** en op beide decks een nummer uit die map. Pauzeer beide decks voor deze proef.
2. Sluit Codex helemaal met **⌘Q**. Alleen het chatvenster verbergen is niet hetzelfde.
3. Dubbelklik in Finder op **Start Jev-proef.command**. De starter opent Bridge en DJ Jev en gebruikt de bestaande sleutel. Hij weigert wanneer Codex nog draait.
4. Laat DJ Jev zonder hulp minstens **drie nummers / twee volledige overgangen** afwerken. Houd Codex afgesloten en bedien Rekordbox tijdens die periode niet.

Kijk en luister of DJ Jev zelf de volgende muziek laadt, de rode maatmarkeringen uitlijnt vóór de fade, de overgang afwerkt, de EQ terugzet op neutraal en daarna opnieuw doorgaat. Er mag geen stilte ontstaan omdat het programma te laat handelt. Als handmatige hulp nodig is, is de proef mislukt; pas de software dan pas na afloop aan. Om de proef te beëindigen kun je de muziek in Rekordbox pauzeren.

De starter bevestigt alleen dat de sessiestart is aangenomen. De melding en vragen in de widget zijn geen bewijs van een geslaagde mix: daarvoor telt wat Rekordbox daadwerkelijk afspeelt. De eigen observaties van het programma kunnen fouten bevatten.

## Terugkijken

- `evidence/standalone-runs/<id>/manifest.json`: starttijd, controle dat Codex gesloten was, Bridge-proces en hashes van programmabestanden. Die controle geldt voor het startmoment; houd Codex zelf afgesloten tijdens de hele proef.
- `evidence/sessions/<id>/session.json`: voorbereide tracks, uitgelijnde starts, bass-stappen, afgeronde overgangen en eventuele fout.
- `evidence/playground-live/session-*.json`: de werkelijk verstuurde Jev-vragen en ontvangen antwoorden van die sessie, zonder API-sleutel.

In deze versie kiest Jev de volgende track en de duur van de overlap. De bewegingen binnen de overgang volgen nog de geprogrammeerde mix. Twee EQ-bewegingen zijn snel opeenvolgende lokale muisgebaren, niet letterlijk gelijktijdig. Een onafhankelijke geslaagde proef bewijst zelfstandig uitvoeren van deze versie, niet dat ieder muzikaal besluit al van Jev komt.
