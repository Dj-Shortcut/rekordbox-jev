> Historisch ontwikkelingsdocument. De actuele werking en proefresultaten staan in [demo/README.md](demo/README.md).

# Jev kiest de volgende handeling

De gewenste DJ beoordeelt doorlopend de situatie, kiest zelf een bedieningshandeling en krijgt daarna terug wat er werkelijk veranderde. De code schrijft geen mixvolgorde voor. `scripts/jev_reactive.py` is de eerste korte proef van die terugkoppeling, met maximaal drie beslissingen op de geladen decks.

De [TypeSafe function-calling-uitleg](https://docs.typesafe.ai/cookbooks/function_calling) is gebruikt voor de koppeling tussen modelkeuzes en bestaande functies. Een Choice bevat hier één concrete handeling inclusief deck en waarde. Er zijn geen vooraf samengestelde mixplannen. De tutorialtechnieken gaan als uitgeschreven achtergrondkennis mee; de oude instructies voor planselectie niet.

## Starten

Gebruik voortaan **Start proef** in **Jev Widget.app**. Bij de eerste keer bewaar je de sleutel daar via **Bewaar sleutel** in macOS Sleutelhanger. De widget haalt Rekordbox zelf naar voren. Zie [WIDGET.md](WIDGET.md). De onderstaande Terminal-route blijft alleen als alternatief beschikbaar.

1. Dubbelklik **Start Jev-proef.command** in deze map.
2. Plak de TypeSafe-sleutel bij de verborgen invoervraag in Terminal en druk Enter. De sleutel wordt niet opgeslagen.
3. Zet Rekordbox vooraan. Zodra dat bevestigd is, begint de proef. De zwevende widget toont de vragen en resultaten.

Na afloop blijft de Terminal-sessie open. Druk daar Enter voor nog een proef met dezelfde sleutel, of typ `q` en druk Enter om af te sluiten. Zolang die sessie open is blijft de sleutel uitsluitend in het procesgeheugen; hij wordt niet op schijf of in Sleutelhanger opgeslagen. Een volgende proef start alleen na jouw Enter.

De proef kan daadwerkelijk starten, pauzeren, tempo aanpassen, het weergegeven tempo synchroniseren of een kanaalfader verplaatsen. De huidige proef heeft faderposities in stappen van 10% van de slag; dat is de resolutie van deze tijdelijke bediening, geen muzikale voorkeur. Jev kiest ook zelf wanneer niets veranderen gepast is. Laden, EQ, loops en effecten zijn nog niet opgenomen in deze kleine proef.

Na elke keuze volgt een controle op gewijzigde tracks, transport, tempo en faders. De gekozen handeling wordt eenmaal verstuurd. De native Bridge levert vervolgens een schermaflezing terug; die wordt gebruikt voor de resultaatcontrole zonder een extra opname. Een fout of onbevestigde bediening stopt de proef. Ctrl+C stopt nieuwe handelingen; een gestarte track wordt bij het einde van de proef niet automatisch gepauzeerd.

## Wat we weten en nog niet weten

De proef gebruikt echte Rekordbox-gegevens. Op 21 september om 16:53 en 16:54 zijn twee proeven via de widget afgerond, met in totaal zes echte antwoorden. De tweede proef volgde na afsluiten en heropenen van de widget, zonder nieuwe sleutelinvoer. API-tijden: 0,69–0,76 seconde; de totale cycli, inclusief opname, duurden 1,13–1,55 seconde. Jev koos steeds `wait`; er zijn geen bedieningshandelingen uitgevoerd. De 37 offline controles slagen. Dit bevestigt de werkende verbinding en sleutelhergebruik, niet het zelfstandig mixen.

De log maakt onderscheid tussen waarnemingstijd, API-tijd, controle vóór de handeling, bediening met teruglezing en de hele cyclus. De netwerkverbinding krijgt een timeout van vijftien seconden om ook langzame antwoorden te kunnen inspecteren. Een oorspronkelijke waarneming ouder dan drie seconden wordt vóór verzending van bediening afgewezen; een ontvangen laat antwoord blijft wel in de widget zichtbaar. Dit zijn grenzen voor de technische proef, geen belofte van beatnauwkeurigheid. De native bedieningsfunctie maakt zelf ook opnames en draagt dus bij aan de vertraging.

De eerste echte reactieve aanvraag stopte na circa 3,8 seconden met de toenmalige algemene netwerkfoutmelding. Er is geen antwoord of handeling vastgelegd. Die oude melding maakte geen onderscheid tussen een timeout en een andere verbindingsfout; zij bewijst niet dat de rekentijd van Jev de oorzaak was. De nieuwe versie registreert dat onderscheid en bewaart ontvangen antwoorden ook wanneer ze te laat zijn voor bediening.

Jev krijgt nu OCR en knopstanden, geen audio. Kickfase, frases, luidheid en de actuele crossfaderrouting zijn onbekend en worden als onbekend meegestuurd. Deze proef bewijst dus nog geen mensachtige hoorbare beoordeling, geslaagde overgang of ononderbroken set. De eerdere Jev-responstijd van 0,895 seconde hoort bij een andere vraag; daaruit volgt niet dat Jev het knelpunt van deze koppeling is.

Alleen een actuele vraag bekijken, zonder API of bediening:

```sh
python3 scripts/jev_reactive.py --preview
```
