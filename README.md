> Actieve ontwikkeling, 22 september: [de nieuwe DJ Jev-kern staat in `demo/`](demo/README.md). `config/live_trial.json` kiest `doom_demo`; de widget start via de bestaande sleutelhouder deze nieuwe kern. De kern importeert geen oude DJ-planner of sessielus. De laatste beoordeelde live run voerde drie opeenvolgende autonome overdrachten inclusief EQ-herstel uit en bleef verder draaien; de gebruiker heeft de proef geslaagd verklaard en laten stoppen.

Daarna zijn rustigere muzikale voorkeuren toegevoegd: later inzetten en langere blends, met ongeveer 32 maten als uitgangspunt. Deze wijziging is lokaal gecontroleerd en nog niet opnieuw beluisterd. Zie de [actuele werking, timing en testresultaten](demo/README.md) voor de details. De widget bevat de meegeleverde transparante achtergrondafbeelding in `assets/dj-jev-background.png`.

De actieve bestanden zijn `demo/djjev/runner.py` (gelijktijdig lezen, Jev en bediening), `policy.py` (de echte vragen en antwoorden), `environment.py` (native opdrachten en controle) en `state.py` (uitlezing). `Sources/Bridge.swift`, `TrackTransport.swift` en `FastObservation.swift` bevatten de fysieke macOS-koppeling. `JevProbeControls.swift` start de reeds draaiende sleutelhouder; de huidige widget leest zelf geen sleutel.

Offline controles van de nieuwe kern: `cd demo && python3 -m unittest discover -s tests`. Deze controles bedienen Rekordbox niet en bewijzen geen echte set. Alle bestanden hieronder buiten `demo/` die oude DJ-keuzes bevatten, blijven uitsluitend als ontwikkelgeschiedenis aanwezig.

## Historische overdracht van 21 september

De volgende status en startinstructies beschrijven de oudere implementatie, niet de actieve `doom_demo`-route.

# Rekordbox + Jev — experimenteel prototype

Privé-overdracht voor menselijke hulp, 21 september 2026. Dit is een opgeschoonde broncodekopie van de lokale ontwikkeling. **Nog geen betrouwbaar autonome DJ.** De bestaande geïnstalleerde apps zijn niet gewijzigd bij deze overdracht.

Doel: muziek uit map `26` selecteren op key en tempo, laden, synchroniseren en zelfstandig mixen in Rekordbox op macOS. Jev moet de muzikale beslissingen nemen; een zwevende widget toont de vragen en antwoorden. Geen standaardloop of vast opgelegde persoonlijke mixstijl. Het is een funproject: houd bediening en hulp eenvoudig.

## Status

- Native Swift-bridge kan Rekordbox lezen via ScreenCaptureKit/Vision en bedienen met toetsen en pointeracties.
- Widget bewaart de TypeSafe-sleutel in macOS Sleutelhanger. De sleutel gaat naar Python via een anonieme stdin-pipe, niet via argumenten of bestanden.
- Echte Jev-antwoorden ontvangen; eerdere HTTP-aanvragen circa 0,69–0,99 s. Eén DJ-aanvraag duurde 6,43 s bij 7.466 invoertokens. Dit is totale HTTP-tijd, geen afzonderlijk gemeten modeltijd.
- Die DJ-proef mislukte: keuze `deck2_play` werd als te oud verworpen. De waarnemingsfase duurde 4,25 s en telde mee in een grens van 3 s.
- Daarna is de tijdmeting aangepast naar het native screenshot-tijdstip en wordt de bibliotheek vóór de opname gelezen. Unit-tests slagen; een geslaagde live Jev-overgang na deze wijziging is **niet bewezen**.
- Latere pogingen liepen vast op vensterzichtbaarheid/voorgrond tijdens voorbereiding. De gebruiker gaf aan ondertussen op de Mac bezig te zijn; niet alle fouten zijn daarmee verklaard.
- Een begeleide overgang **zonder Jev** is uiteindelijk uitgevoerd: beide decks gestart, crossfader in twintig stappen overgezet, uitgaand deck daarna gepauzeerd. Eindstand visueel bevestigd. Dit bewijst bediening, geen autonome DJ, betrouwbare beatfase of onafhankelijke beoordeling van audiokwaliteit.

Zie [HANDOFF.md](HANDOFF.md) voor de concrete problemen.

## Ontwikkeling

Getest met Rekordbox 6.8.7 op Apple Silicon. Swift-builds richten zich op macOS 14+. Benodigd: Xcode command-line tools en Python 3.10+. Alleen de optionele MP3-energieanalyse vereist `numpy` en `soundfile`.

```sh
python3 -m unittest discover -s tests -v
bash scripts/build.sh
bash scripts/build-reader.sh
bash scripts/build-widget.sh
mkdir -p evidence
```

Open de gebouwde apps via Finder. De bridge vraagt lokale macOS-rechten voor toegankelijkheid en schermopname. **Herbouw de bestaande werkende bridge op de oorspronkelijke Mac niet zonder reden:** gewijzigde ad-hoc ondertekening kan die rechten ongeldig maken.

Muziek blijft lokaal onder `~/Music/Music/26`. Optionele metadata komt uit `~/Desktop/rekordbox.xml`. `python3 scripts/inventory.py` maakt de lokale inventaris. Er zitten geen muziekbestanden, export, schermafbeeldingen of echte aanvraaglogs in deze repo.

`scripts/controller.py state` leest alleen. Play/fader/demo-commando's bedienen de echte app. `scripts/jev_reactive.py --preview` bereidt alleen een vraag voor. De widgetknop start een live proef met bediening; niet gebruiken als offline test.

## Bestanden

- `Sources/Bridge.swift`: macOS-bridge, socket, OCR, toetsen en pointeracties.
- `Sources/ReadFrame.swift`: pixelmetingen voor mixer en indicatoren.
- `Sources/JevWidget.swift`, `Sources/JevControls.swift`: zwevende widget, Sleutelhanger en processtart.
- `scripts/jev_reactive.py`: actuele observeer/beslis/bedien-proef.
- `scripts/controller.py`, `scripts/music_context.py`: normaliseren, controleren, muziekgegevens.
- `config/mixing_guidance.json`: uitgeschreven techniekbeschrijvingen met bronverwijzingen.
- `scripts/jev_decisions.py` en synthetische `examples/`: oudere plan-adapter, gebruikt door contracttests; niet de actuele live besturing.
- `scripts/demo*.py`: begeleide experimenten, geen autonome DJ. De laatste geslaagde directe crossfade was een eenmalige uitvoering; `demo_without_jev.py` bleef eerder op een foutieve sync-aflezing steken.

## Geheimen

De API-sleutel is niet uit Sleutelhanger opgehaald voor deze export. Alleen expliciet geselecteerde tekstbestanden zijn gekopieerd naar een nieuwe Git-geschiedenis. `.gitignore` sluit credentials, logs, lokale data en gebouwde apps uit. Voor de eerste push is de export met Gitleaks gecontroleerd. Een helper gebruikt een eigen sleutel; zet nooit een echte sleutel in code, issues of chat.

TypeSafe-werkwijze: [officiële skill](https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md) en [documentatie](https://docs.typesafe.ai/).
