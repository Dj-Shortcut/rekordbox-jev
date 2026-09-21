# Overdracht aan de helper

De gebruiker heeft verdere DJ-bediening laten stoppen en menselijke hulp gevraagd. Deze repo is een broncodesnapshot, geen bewezen werkend product. Vraag niet opnieuw om de bestaande sleutel: die staat al in macOS Sleutelhanger voor de lokale widget.

## Eerst onderzoeken

1. **Onbetrouwbare waarneming.** Tijdens de laatste begeleide proef stond op de jogdisplay van deck B 124.00, maar `controller.normalized_state` rapporteerde soms 125.0. Onderzoek de regio en volgorde van de OCR-teksten: het originele tempo staat ook in beeld. `ReadFrame.swift` verwacht blauwe pixels voor Beat Sync; de waargenomen actieve tekst was wit. De indicator kan daardoor onterecht `false` zijn. Niet blind een sync-toggle herhalen op die aflezing.
2. **Voorgrond en vensterlevensduur.** De native bridge meldde soms geen zichtbaar hoofdvenster; Computer Use liet soms een oude menubalk of geen screenshot zien. Rekordbox-vensters en indelingen wisselden. Ook een zwevende widget kan inputposities afdekken. Verifieer het daadwerkelijke venster en de inputontvanger. Het verdwijnen was niet een bewezen Jev-fout.
3. **Timing.** De 6,43 s was één uitschieter; de client meet alleen de hele HTTP-aanroep. Scheid verbinding/opsturen/wachten/ontvangen voordat je de oorzaak benoemt. Bewaar alle pogingresultaten. Verhoog niet alleen de timeout tot een demo toevallig slaagt.
4. **Actualiteit.** `capture_time` gebruikt native `DispatchTime.uptimeNanoseconds` naast Python `monotonic`. Eén live vergelijking gaf circa 0,545 s opnameleeftijd. De 3 s-grens is niet verhoogd. De nieuwe afwijzingslogica mist nog gerichte regressietests voor oud/toekomstig tijdstip en trage voorbereiding.
5. **Testprotocol.** De DJ-test wacht nu op No Rules spelend met 45–65 s over, Sun gepauzeerd binnen de eerste seconde, open kanaalfaders en crossfader links. De voorbereiding herhaalt alleen waarneming bij tijdelijk onzichtbaar venster. Die voorwaarden en de latere tijdmetingswijziging zijn nog niet samen live succesvol doorlopen. De proef is beperkt tot één overgang; geen automatische selectie/laden/EQ-keten.

## Niet verwarren met succes

- Unit-tests testen contracten en uitzonderingen, geen hoorbare overgang.
- `dispatched` is alleen verzonden input; `verified` is teruggelezen UI.
- Gelijke BPM is geen bevestigde kickfase.
- De widget kan “Proef afgerond” tonen terwijl uitvoering mislukt is; lees de uitvoeringsstatus.
- `demo_without_jev.py` documenteert een poging die stopte bij sync-aflezing. De later uitgevoerde crossfade slaagde met directe begeleiding na visuele beoordeling van de jogdisplay; deze repo verbergt het eerdere falen niet.

## Overdrachtsgrens

Geen binaries, bibliotheek-export, MP3's, audioanalyse, screenshots, echte API-antwoorden, sleutel of oorspronkelijke Git-geschiedenis geüpload. Synthetische voorbeelden zijn expliciet als zodanig gemarkeerd. De twee tracknamen in de proefcode zijn vaste testfixtures. De lokale bestanden en werkende appbundels staan nog op de oorspronkelijke Mac.
