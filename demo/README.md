# DJ Jev — lokaal prototype

Doel: met één Start-knop een doorgaande set draaien in Rekordbox, met Jev als beslisser. **De laatste beoordeelde live run heeft drie opeenvolgende overdrachten inclusief EQ-herstel uitgevoerd en bleef verder draaien.** Dat bewijst de functionele keten op dat meetmoment; het is geen garantie voor iedere volgende overgang of een muzikaal afgewerkte DJ-set.

## Welke delen wat doen

- `djjev/runner.py` laat waarneming, maximaal één Jev-aanvraag en maximaal één bediening onafhankelijk lopen. Lezen en de UI gaan door tijdens aanvragen en laadhandelingen. Verouderde antwoorden worden verworpen; na bediening is een nieuwe waarneming nodig.
- `djjev/policy.py` biedt keuzes op basis van de actuele toestand. Jev kiest transport, opvolger en mixerdoelen. Code controleert onder meer map 26, trackidentiteit, key/tempo, routes en rode-beatuitlijning. Er is geen vaste nummerlijst of mixfasevolgorde.
- `djjev/environment.py` vertaalt gekozen acties naar native bediening en controleert de zichtbare uitkomst. Nieuwe screenshots bevestigen veranderingen zonder dezelfde muisactie blind te herhalen. Onbekende of gedeeltelijk uitgevoerde fouten blokkeren verdere bediening.
- `djjev/jev_client.py` gebruikt `jev-latest` via een blijvende HTTPS-verbinding. De bestaande hoofd-Bridge geeft de reeds toegestane sleutel eenmaal aan de sessie; native lees- en bedieningsprocessen krijgen die sleutel niet. Codex maakt tijdens de lus geen DJ-keuzes.

De gelijktijdige taken volgen het idee uit de [openbare Doom-implementatie van AmoghCreator](https://github.com/AmoghCreator/doom-jev/blob/main/main.py). Deze Python-kern importeert geen oude DJ-planner of sessielus.

## Antwoord, uitvoering en bevestiging

`transport=mix` kiest expliciet een mixerbeweging. Afzonderlijke fader-, bass- en duurvragen veronderstellen **“als MIX gekozen wordt”**; alleen bij `mix` worden hun antwoorden gebruikt. Een trackantwoord wordt alleen gebruikt bij `load_A` of `load_B`. De widget bewaart de echte antwoorden en kansen, maar toont ongebruikte takken gedimd als **“Niet uitgevoerd · Jev koos …”**, onder **“Bij mengen”** of **“Bij laden: welk nummer?”**. Een modelantwoord is geen uitvoeringsbevestiging.

Bevestigde betekenisvolle acties en recent geladen tracks blijven apart van HOLD bewaard. Na een bevestigde start van een onhoorbare opvolger onthoudt de runner het inkomende en uitgaande deck en beide trackidentiteiten. Die context overleeft HOLD-keuzes en vervalt bij andere trackidentiteiten. Jev krijgt resterende tijd, fader-eindpunt en EQ-herstel mee om zelf verder te kiezen.

Zijn beide decks bevestigd gestopt, dan kan de app de route naar het door Jev gekozen geladen deck openen en dat starten; er is geen vast openingsdeck. Gewone mixbewegingen vereisen uitgelijnde rode markers. Fader en bass kunnen binnen één gekozen beweging veranderen; de ene muiscursor voert de kleine acties lokaal na elkaar uit.

Trackherkenning probeert eerst een exacte titel. Een afgekorte titel mag alleen via een uniek letterlijk begin van minstens 32 tekens aan één bibliotheektrack worden gekoppeld. De oorspronkelijke waargenomen titel blijft bewaard; de herkenning vult geen gegokte woorden aan.

De native laag plant Play lokaal op een toekomstige maatgrens. Als voorbereidend werk die grens mist zonder Play te versturen, observeert en plant zij opnieuw, maximaal vier keer. Een reeds verstuurde Play wordt niet herhaald. Geplande en werkelijke verzendtijden worden afzonderlijk gelogd.

## Rustiger muzikale timing

Na de geslaagde controleproef is de voorkeur veranderd naar een vloeiendere set. Jev krijgt opdracht om de opvolger vroeg klaar te zetten, maar de huidige track langer te laten spelen. Als uitgangspunt geldt een blend van ongeveer **32 maten** (circa 62 seconden bij 124 BPM), met 16 maten als korter alternatief wanneer de resterende tijd dat vraagt. Het geschatte instapvenster begint in de laatste 48 maten van de uitgaande track. Dit zijn instelbare codevoorkeuren in `djjev/musical_timing.py` en de instructies in `djjev/policy.py`; Jev blijft kiezen en een naderend einde krijgt voorrang.

De runner onthoudt wanneer beide decks bevestigd hoorbaar gemengd stonden. Jev ziet de verstreken overlap en kan de blend langer aanhouden met HOLD tussen korte fader-/bassbewegingen. Het aantal antwoorden of de eerder onhoorbare start telt niet als mixtijd. Pauzeren, de route sluiten, seeken, opnieuw uitlijnen of andere tracks laden maakt die tijdregistratie ongeldig. Afzonderlijke native bewegingen en de rode-markerbeveiliging zijn ongewijzigd.

Voor een beter instapmoment krijgt Jev een **geschatte groepering van 16 maten** uit het geëxporteerde beatgrid. Dit herkent geen drop of breakdown. Bij onbekend/variabel grid of gewijzigd tempo blijft de schatting onbekend; Jev mag dan op het tijdvenster en de bestaande inzet op een maatgrens terugvallen. De native laag plant nog steeds op de eerstvolgende zichtbare maatgrens, niet op een verre frasegrens.

Deze timingwijziging is met lokale regressietests gecontroleerd; er is daarna geen nieuwe hoorbare set gestart. Het live bewijs hieronder betreft de eerdere snellere spelvoorkeuren.

## Gemeten bewijs — 22 september 2026

In live run `b36f091a-00bc-4049-b724-62fc385d0332` zijn op het laatst beoordeelde moment deze overdrachten bevestigd:

1. Noche Clara (Extended) → Ifthah.
2. Ifthah → Heaven.
3. Heaven → Noche Clara (Extended).

Elke overdracht bevat waargenomen uitgelijnde overlap, een bevestigde fysieke mixbeweging, het fader-eindpunt en alle EQ-banden van beide decks terug neutraal. De run was bij die controle niet gestopt en had geen gelogde fouten. Latere resultaten vallen buiten deze momentopname.

Bij de eerste twee geplande inzetten werd het toets-event respectievelijk **1,73 ms en 5,09 ms** na de geplande tijd verstuurd. Dat is lokale eventtiming, geen meting van de daadwerkelijke audioaanvang. De eerste zestien Jev-antwoorden duurden 0,35–1,28 s, mediaan 0,37 s; eerdere runs hadden ook aanvragen boven 7 s. Er is geen vaste latencygarantie.

De keuzes in deze proef mixten tracks nog zeer vroeg door. De functionele keten werkt in deze proef; de hierboven beschreven rustigere timing is later toegevoegd en nog niet hoorbaar beoordeeld. De app leest screenshots en exportmetadata uit map 26; zij luistert niet naar audio. EQ-hoek is geen dB-meting en frases, drops of luidheid worden niet uit titels afgeleid.

Beslisreplays met echte Jev-antwoorden gaven eerder 7/8 (`8f7d50ac`), daarna 2/2 gerichte controles (`9d11de6a`) en 3/3 EQ-keuzes op opgeslagen werkelijke toestanden (`03cb802e`). Die replays bedienden niets. Unit- en integratietests gebruiken gesimuleerde API/native antwoorden; zij bewijzen softwaregedrag, geen muzikale kwaliteit.

## Bewijs teruglezen

```sh
python3 -m unittest discover -s tests -v
python3 scripts/summarize_run.py evidence/<run-id>
python3 scripts/summarize_run.py evidence/<run-id> --json
```

De samenvatter doet geen API-call en bedient niets. Hij onderscheidt fysieke bevestigingen van HOLD en toont native foutdetails. `events.jsonl` bewaart vragen, antwoorden, uitvoering en terugkoppeling afzonderlijk. `evidence/` en credentials worden niet gepubliceerd. Stop beëindigt verdere bediening zonder op de afspeelknoppen van Rekordbox te drukken.
