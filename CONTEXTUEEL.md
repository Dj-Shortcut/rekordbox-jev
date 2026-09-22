# Actuele waarneming naar Jev — 22 september 2026

De eerste vraag is gekoppeld aan een echte Rekordbox-uitlezing. In een zelfstandige child van de bestaande, ondertekende Bridge zag de nieuwe adapter twee expliciete `Not Loaded`-meldingen. De planner stelde daarop de vraag welk nummer uit map 26 moest openen. Jev koos **Attract — Ajna (BE)/Samm (BE)**. De test verstuurde geen bedieningscommando's.

Dit is de koppeling van observatie naar vraag naar echt modelantwoord. Het is nog geen volledige autonome DJ-sessie. De widget heeft nu **Start proef** voor deze alleen-lezen proef; de actieve configuratie is `contextual_observer`. De widgetroute weigert oude bedieningsmodi.

## Bestanden en verantwoordelijkheden

- `scripts/contextual_observation.py` vertaalt de native OCR/pixelgegevens en exportmetadata. Iedere veldwaarde heeft herkomstinformatie. Onbekende waarden blijven onbekend.
- `scripts/contextual_planner.py` is een ongewijzigde kopie van de eerder geteste, zuivere Playground-planner. Hij retourneert een codevoorstel of een Jev-vraag met meerdere opties.
- `scripts/contextual_live.py` leest één verse waarneming, maakt de relevante vraag, verstuurt die eenmaal, bewaart het onbewerkte antwoord en leest de toestand nogmaals. Het bestand heeft geen uitvoerder voor afspelen, laden, EQ of faders. De live prompt voegt echte herkomst toe en vervangt de verwijzing naar synthetische EQ-data door de werkelijke meetbeperking.
- `scripts/jev_reactive.py` ondersteunt de tijdelijke modus `contextual_observer`, zodat de bestaande Bridge zijn opgeslagen sleutel via de bestaande pipe kan doorgeven. Er is geen nieuwe sleutelopslag, sleuteluitvoer, native build of toestemmingswijziging.

## Live resultaat

Op 22 september 2026 kreeg Jev de werkelijke lege-decktoestand en 127 kandidaten uit de bestaande map-26-export. Model `jev-1.13.0` koos Attract: 123 BPM, D mineur. De volledige aanvraag duurde 4,432 s; de oorspronkelijke waarneming was bij antwoord 4,889 s oud. Dat overschrijdt het bestaande budget van drie seconden voor toepassing op een oude waarneming. De tweede uitlezing gaf dezelfde deck- en mixerwaarden. Er is niets uitgevoerd, ook niet op basis van die hercontrole.

De vraag gebruikte 26.025 invoertokens en 3.657 uitvoertokens, inclusief de kansverdeling over 127 opties. De gekozen kans was 0,14 en de gerapporteerde confidence 0,12. Dit bewijst een geldige modelkeuze bij de eerste vraag; het bewijst niet dat dit muzikaal de beste opener is of dat zo'n grote keuzevraag geschikt is vlak voor een mixactie.

De lege toestand is positief herkend aan `Not Loaded` plus lege BPM/metadata en een niet-actieve play-indicator. Een lege OCR-titel op zichzelf wordt nooit als leeg deck behandeld. Frasegrenzen, zang en de richting/hoeveelheid van een niet-neutrale bass-EQ worden nog niet uitgelezen. De adapter vult die niet in. Twee al geladen decks of een lopende overgang kunnen daarom nog aanvullende sessiegegevens of metingen vereisen; deze integratie doet daarover geen geslaagde live claim.

## Valideren zonder Rekordbox te bedienen

```sh
python3 -m unittest discover -s tests -p 'test_contextual*.py' -v
python3 -m unittest discover -s tests -p 'test_jev_reactive.py' -q
```

De API-aanroep gebruikt de sleutel uit de bestaande ondertekende Bridge. Klik op **Start proef** in de widget. `config/live_trial.json` staat op `{"implementation":"contextual_observer"}`; de widgetroute weigert andere modi. Het live resultaat staat lokaal onder `evidence/contextual-live/<run-id>/result.json`; de widget ontvangt hetzelfde echte antwoord via de bestaande eventlog. Er is geen automatische herhaling bij een fout.

De volgende implementatiestap is geselecteerde trackidentiteit en sessierollen veilig doorgeven aan laden en opnieuw waarnemen. De muzikale mixvragen hebben nog echte invoer nodig voor de ontbrekende signalen; de synthetische Playground-resultaten vervangen dat niet.
