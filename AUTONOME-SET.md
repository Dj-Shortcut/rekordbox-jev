# DJ Jev: starten met één knop

De widget **DJ Jev** heeft één knop: **Start** wordt **Stop** zolang de set actief is. Start gebruikt de bestaande Rekordbox Bridge en de daarin beschikbare TypeSafe-sleutel. De ingestelde route is `dj_set`; er is geen limiet van één nummer of dertig seconden. Stop beëindigt verdere bediening en laat spelende muziek ongemoeid. Bij het opnieuw openen koppelt de widget aan een reeds actieve sessie.

Dit beschrijft de gebouwde werking. **Een volledige echte autonome set met deze versie is nog niet aangetoond.** De geautomatiseerde controles gebruiken nagebootste providerantwoorden en bediening; zij bewijzen geen live timing of muzikale kwaliteit.

## Wie beslist en wie bedient?

- [`scripts/dj_brain.py`](scripts/dj_brain.py) maakt een vraag uit de actuele decks, resterende tijd, mixerstanden, recente handelingen en de metadata van map 26. Jev kiest uit de op dat moment uitvoerbare handelingen. Trackkeuze en bewegingsduur worden, wanneer relevant, als onafhankelijke vragen in dezelfde aanvraag meegenomen. Er is geen vaste reeks mixfasen die Jev alleen mag goedkeuren.
- [`scripts/dj_set.py`](scripts/dj_set.py) herhaalt **uitlezen → Jev vragen → opnieuw controleren → uitvoeren → opnieuw vragen**. Het programma vervangt een ontbrekend antwoord nooit door een eigen muziekkeuze. Dezelfde sleutel en HTTPS-verbinding worden binnen de sessie hergebruikt. Na een verbindingsfout volgt een nieuwe vraag met actuele informatie; een oude fysieke opdracht wordt niet opnieuw afgespeeld.
- [`scripts/dj_controls.py`](scripts/dj_controls.py) voert de gekozen begrensde bediening uit. De Swift Bridge verzorgt schermopname, OCR, visuele metingen en muis-/toetsenbediening. Zij controleert trackidentiteit, uitlijning en deadlines bij de handeling zelf. Gekoppelde EQ-bewegingen gebeuren lokaal vlak na elkaar met één muisaanwijzer, zonder netwerkvraag ertussen.
- [`scripts/dj_transport.py`](scripts/dj_transport.py) kan een bijgewerkte bedieningsworker gebruiken terwijl de bestaande sleutelhost blijft draaien. De worker ontvangt geen API-sleutel en weigert sleutel- en sessiecommando's. Daardoor hoeft een wijziging aan de bediening de reeds geautoriseerde sleutelhost niet te herstarten.

De widget toont daadwerkelijk verzonden vragen, originele Jev-antwoorden met tijdstip en kansen, en apart de uitvoering. Een antwoord is dus niet hetzelfde als een bevestigde handeling. De eventuele track- en duurantwoorden worden alleen gebruikt wanneer de bijbehorende handeling is gekozen.

## Grenzen die nu gelden

De koppeling leest Rekordbox visueel uit en gebruikt geëxporteerde trackmetadata en beatgrids. Zij luistert niet naar de muziek en herkent geen zang, drops of muzikale frases. Jev krijgt die ontbrekende informatie ook niet als gemeten feit voorgesteld.

Alle muziek komt uit map 26. Geladen en recent gebruikte tracks worden uitgesloten; de kandidaatselectie controleert key, tempo en de beschikbare beatgrid voor een gesynchroniseerde inzet. Een faderbeweging of hoorbare basswissel vereist bevestigde uitlijning van de rode beatgroepen. Knopstanden zijn visuele posities, geen gemeten dB of geluidssterkte.

Bij tijdelijk onleesbare informatie of een ontbrekend API-antwoord blijft de lus opnieuw waarnemen terwijl bestaande muziek verder speelt. Bij een verstuurde handeling waarvan het resultaat niet betrouwbaar is bevestigd, stopt verdere bediening. Dit is geen garantie dat er onder iedere storing nooit stilte kan ontstaan.

## Verificatie

De tests controleren onder meer drie opeenvolgende tracks met twee overgangen via de echte beslis- en runnercode, het verwerpen van achterhaalde antwoorden, veilige deckvervanging, beatgrid-start, geen faderbeweging bij ongelijke rode dots, gekoppelde bassrichting en neutraal herstel, en scheiding van bedieningsworker en sleutelhost. Alleen provider en fysieke Rekordbox-bediening zijn daarin nagebootst.

```sh
python3 -m unittest discover -s tests -p 'test_dj_*.py' -v
```

Lokale ruwe aanvragen, antwoorden en uitvoeringstijden worden tijdens een run onder `evidence/` bewaard. Deze gegevens, appbundels, muziek en sleutels horen niet in de bronrepository. De bestaande `.gitignore` sluit ze uit.
