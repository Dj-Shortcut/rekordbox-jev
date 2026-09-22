> Historisch ontwikkelingsdocument. De actuele werking en proefresultaten staan in [demo/README.md](demo/README.md).

# Jev: muzikale keuzes op basis van tutorials

**Dit document beschrijft de eerdere mixplan-adapter.** De gewenste richting is inmiddels een actieve DJ die telkens de volgende handeling kiest op basis van een nieuwe waarneming. De eerste korte implementatie daarvan staat in [REACTIEF.md](REACTIEF.md); de oude adapter blijft alleen voor de bewaarde API-test beschikbaar.

De uitleg van Crossfader is de technische basis. Losse smaakantwoorden uit de chat zijn niet als persoonlijke mixregels opgenomen.

Bronnen:

- [How to mix EDM — Three Easy DJ Mixing Techniques](https://wearecrossfader.co.uk/blog/how-to-mix-edm/)
- [3 Ways To Mix House Music — EQ Swap](https://blog.wearecrossfader.co.uk/blog/3-ways-to-mix-house-music/)

`config/mixing_guidance.json` bevat nu de volledige uitgeschreven beslis- en bedieningsspecificatie: begrippen, benodigde muziekgegevens, keuzeprocedure, vier technieken met zeventien stappen, timingregels en uitvoervoorwaarden. Het afzonderlijke techniekbestand is erin opgenomen. Dit is een verzameling keuzemogelijkheden; geen vaste volgorde voor iedere overgang. De looptechnieken worden niet automatisch geactiveerd.

## Verdeling van het werk

1. Muziekanalyse levert echte trackkenmerken, cuepunten, frasewissels en drops. Ontbrekende kenmerken blijven onbekend.
2. De code stelt mogelijke combinaties van track, techniek en muzikaal overnamemoment samen. Voor deze stap ontbreekt nog de koppeling met structurele muziekanalyse.
3. Jev kiest tussen complete voorstellen, of geeft aan dat er geen passend voorstel is. De huidige adapter verwacht die voorstellen als invoer; hij verzint zelf geen markeringen en kiest geen favoriet vooraf.
4. De lokale uitvoerder moet later de gekozen handelingen plannen en uitvoeren, met actuele controle van track, beatfase, beschikbare tijd en bedieningsmogelijkheden. Die koppeling is nog niet gemaakt.

Jev kiest dus geen losse faderstand op elke schermaflezing. Het model beoordeelt een muzikaal plan, inclusief techniek en overnamemoment. Het bewegen van de knoppen en de precieze klok blijven lokaal.

## Wat nu aanwezig is

`scripts/jev_decisions.py` bereidt een verzoek voor de [TypeSafe HTTP API](https://docs.typesafe.ai/api) voor en bevat de HTTP-aanroep naar `jev-latest`. De implementatie volgt het actuele Choice-formaat en de [function-calling cookbook](https://docs.typesafe.ai/cookbooks/function_calling).

De samenhangende planopties worden in één Choice aangeboden. Zo kunnen track, techniek en timing niet als drie onderling strijdige antwoorden terugkomen. `defer` blijft beschikbaar wanneer geen voorstel past. Confidence en kansverdeling worden behouden; er is geen onbeproefde confidence-drempel die het draaien vrijgeeft.

De adapter controleert scope, markeringen, antwoordkeuzes en request-identiteit. Te late of ongeldige antwoorden leiden niet tot bediening. De netwerk-timeout is geen garantie voor realtime uitvoering: deze module is voor planning en bestuurt Rekordbox niet.

De zwevende [Jev-widget](WIDGET.md) toont de exacte vragen en antwoorden. Evaluaties via de CLI schrijven de vraag en de afloop automatisch naar een lokaal log, dat de widget elke seconde bijwerkt. De viewer vraagt zelf geen API-sleutel en start geen modelaanroep.

Er zijn zestien offline controles voor de Jev-adapter toegevoegd; samen met de vijf bestaande tempocontroles slagen ze. De tests gebruiken een expliciete testtransportfunctie. Ze bewijzen het verzoek- en antwoordformaat van onze code, niet de kwaliteit, toegang of werkelijke latency van Jev.

## Voorbeeld bekijken

Vanuit deze map:

```sh
python3 scripts/jev_decisions.py prepare \
  --context examples/jev-context.json \
  --plans examples/jev-plans.json \
  --output examples/jev-request.json
```

De tracks en tijdmarkeringen in deze voorbeelden zijn **verzonnen voor het formaatvoorbeeld**. Er is geen modelantwoord ingevuld en geen Jev-aanroep gedaan.

Voor een echte API-evaluatie moet `TYPESAFE_API_KEY` via de lokale omgeving beschikbaar zijn. Het script leest de sleutel zonder die te loggen of naar een bestand te schrijven:

```sh
python3 scripts/jev_decisions.py evaluate --request examples/jev-request.json
```

Dit verstuurt het voorbereide voorbeeld naar TypeSafe en kan API-verbruik veroorzaken. Ook een geslaagd modelantwoord bedient Rekordbox niet. Voor toepassing op echte tracks zijn onderbouwde muziekgegevens en de koppeling naar de lokale uitvoerder nog nodig.

## Huidige grens

De eenmalige test via verborgen Terminal-invoer is geslaagd. Jev 1.13.0 antwoordde in 0,895 seconde op het synthetische voorbeeld en koos `defer` (uitstellen), met kans 0,53 en confidence 0,30. Het antwoord bevat geen uitleg voor die keuze. Het resultaat is gecontroleerd tegen het oorspronkelijke verzoek en staat in `evidence/jev-live-example.json`; dat oorspronkelijke verzoek is bewaard als `evidence/jev-live-example.request.json`. Dit bewijst API-toegang voor die test, niet de kwaliteit van beslissingen over echte tracks of blijvend beschikbare authenticatie. De sleutel werd niet opgeslagen. De native Bridge is bij deze test niet bediend.

## Eenmalig testen met verborgen sleutelinvoer

```sh
python3 scripts/jev_decisions.py evaluate --request examples/jev-request.json --prompt-key --output evidence/jev-live-example.json
```

Voer dit uit in een echte Terminal. Plak de sleutel pas bij de verborgen invoervraag. De sleutel komt niet in de opdrachtregel of het resultaatbestand en wordt niet opgeslagen. Dit doet één API-evaluatie met het synthetische voorbeeld; alleen het modelresultaat wordt bewaard.

## Uitgeschreven instructies, versie 2

De eerdere aanvraag bevatte korte techniekbeschrijvingen en algemene principes. Versie 2 neemt het volledige `config/mixing_guidance.json` op onder `payload.state.tutorial_guidance`. Per techniek staan het muzikale doel, de geschiktheid, de volgorde van handelingen, afwijsredenen en benodigde bediening uitgeschreven. De links staan alleen als bronvermelding vermeld. Jev krijgt de tekst zelf; deze adapter laat het model geen webpagina ophalen.

Ook de planningstaak is verduidelijkt: Jev beoordeelt een muzikaal voorstel voor latere uitvoering. Of de software klaar is om het uit te voeren, wordt apart behandeld. Een ontbrekende actuele beatfase mag niet worden verward met onbekende muziekstructuur: het eerste verhindert uitvoering, het tweede kan ook een muzikale keuze verhinderen. De geconstateerde keuze `defer` uit de eerste test had geen uitleg; deze aanpassing bewijst niet waarom Jev toen die keuze maakte.

`examples/jev-request.json` bevat het complete nieuwe verzoek. Een offline controle inspecteert de daadwerkelijk geserialiseerde HTTP-body en bevestigt dat alle instructies worden meegestuurd. Deze uitgebreide versie is nog niet opnieuw aan de echte API aangeboden. De eerdere geslaagde verbindingstest blijft geldig als bewijs van toegang, maar is geen evaluatie van deze nieuwe uitleg.
