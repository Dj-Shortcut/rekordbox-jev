Onderzoek Jev-gamedemo’s — 22 september 2026

De openbare broncode ondersteunt het gewenste principe: een zelfstandig draaiende app leest de actuele toestand, vraagt Jev om de volgende beslissingen en voert die uit, terwijl de app blijft draaien. Codex hoeft daar niet tussen te zitten. Dit onderzoek bestond uit broncode lezen en bestaande lokale meetgegevens controleren; de externe spellen zijn niet opnieuw uitgevoerd en er zijn geen nieuwe betaalde API-aanvragen gedaan.

| Voorbeeld | Wat de bron laat zien | Betekenis voor DJ Jev |
| --- | --- | --- |
| [TypeSafe’s officiële Doom-demo](https://typesafe.ai/blog/introducing-system-one-models-and-jev) | TypeSafe meldt circa tien aanvragen per seconde, met gestructureerde tekstgegevens over de speltoestand. De publicatie kondigt de technische walkthrough nog aan. | Frequent opnieuw beslissen is een bedoeld gebruik. Tien calls per seconde is hier een leveranciersclaim, geen op deze Mac gemeten snelheid. |
| [Mario — loop](https://github.com/fhshaik/typesafe-mario/blob/main/src/typesafe_mario/runner.py#L98-L160), [vragen](https://github.com/fhshaik/typesafe-mario/blob/main/src/typesafe_mario/policy.py#L48-L108) | In dashboardmodus loopt de emulator door met maximaal één API-aanvraag tegelijk. Minstens acht frames en het vorige antwoord moeten voorbij zijn voor de volgende aanvraag. Eén call bevat drie vragen: controlleractie, springen en gevaar. De gewone headless modus wacht wel. | Neem de doorlopende dashboardlus als voorbeeld. Houd actuele waarneming en uitvoering beschikbaar terwijl een nieuwe keuze onderweg is. |
| [Open Doom-implementatie](https://github.com/AmoghCreator/doom-jev/blob/main/main.py), [client](https://github.com/AmoghCreator/doom-jev/blob/main/agent/jev_client.py) | Een andere maker gebruikt 35 spelupdates per seconde, een mogelijke aanvraag per vier updates en maximaal één lopende aanvraag. Zes vragen delen één call en één hergebruikte HTTP-client. | 8,75 calls/s is een maximum uit de code, geen gemeten resultaat. Meerdere vragen hoeven geen opeenvolgende netwerkvertragingen te veroorzaken. |
| [RALLY tafeltennis](https://github.com/Icohen007/jev-play-ping-pong), [uitvoering](https://github.com/Icohen007/jev-play-ping-pong/blob/main/src/cli.mjs), [resultaat maker](https://github.com/Icohen007/jev-play-ping-pong/blob/main/docs/VERIFIED_RUN.md) | Twee vragen per terugspeelbeslissing: richting en kracht. De normale modus pauzeert het spel niet; een nieuw beeld controleert of het antwoord nog op tijd is. Maker rapporteert 124 beslissingen, mediaan 325 ms, maximum 889 ms, nul late acties in één gewonnen wedstrijd. | Vraag zodra de relevante situatie ontstaat, vóór de deadline. Bewaar zichtbaar het verschil tussen antwoord ontvangen en actie uitgevoerd. Dit is één gerapporteerde run, geen eigen herhaling. |
| [Camera-drone](https://github.com/RomanSlack/jev-drone), [worker](https://github.com/RomanSlack/jev-drone/blob/main/tactics.py) | Lokale beeldverwerking maakt symbolische gegevens uit diepte en segmentatie. Jev beoordeelt manoeuvre, risico en verloren doel samen. Een achtergrondworker en een kleine wachtrij houden besturing onafhankelijk van de API. Ongewijzigde situaties worden overgeslagen; lokale besturing kan Jev overrulen. | Bruikbaar voor scheiding van waarnemen, beslissen en nauwkeurig uitvoeren. De simulatorcamera met diepte/segmentatie is niet gelijk aan een gewone Rekordbox-screenshot. |

Jev accepteert momenteel tekst/JSON, geen screenshots, geluid of video. Een lokale uitleeslaag blijft dus nodig. Dat beperkt Jev niet tot browsers: een programma op macOS kan de API aanroepen en de gekozen actie uitvoeren. [Actuele TypeSafe-documentatie](https://docs.typesafe.ai/concepts/state).

De bekeken voorbeelden zijn geen reden om ieder zichtbaar animatieframe een nieuwe DJ-vraag te sturen. Ze laten wél zien dat een doorlopende besliscyclus met kleine, relevante vragen werkt als programmeerpatroon. Het aantal vragen per call en het aantal calls per seconde zijn verschillende grootheden. Meer calls maken een afzonderlijke aanvraag niet sneller; nieuwe calls maken nieuwe beslissingen op basis van nieuwere informatie mogelijk.

Onze laatste echte startproef:

| Stap | Gemeten tijd |
| --- | ---: |
| Eerste schermuitlezing | 1,322 s |
| Map 26 controleren | 0,574 s |
| Jev-aanvraag inclusief transport | 0,973 s |
| Scherm opnieuw lezen na antwoord | 0,487 s |
| Laden en bevestigen | 2,899 s |
| Afspelen en bevestigen | 1,480 s |
| Totaal tot blokkering, inclusief overige overhead | 7,818 s |

Er was één Jev-vraag, één antwoord en twee bevestigde deckacties. Ongeveer 88% van de tijd zat buiten de API-aanvraag. Daarna stopte onze code op ontbrekend geïnterpreteerd tempo/key; de BPM-tekst bevatte ook het pitchpercentage. Dit was geen Jev-timeout. [Ongewijzigd lokaal resultaat](../rekordbox-bridge/evidence/contextual-session/79c86c6b-45a5-4711-89e0-54a098eb628a/result.json).

De koppeling maakt herhaaldelijk volledige screenshots met nauwkeurige OCR. Laden heeft meerdere afzonderlijke controles. De nieuwe continue sessielus is bovendien nog niet aangesloten op een bestaande `contextual_controls.py`; de launcher verwacht nog een oude eindstatus. Deze werkversie is dus niet klaar voor een nieuwe volledige live mixproef. De losse geslaagde tests betekenen dat ook niet.

De concrete richting uit het onderzoek: één blijvend lokaal proces, compacte actuele deckgegevens, Jev-vragen zodra een volgende keuze relevant wordt, onafhankelijke vragen samen versturen, en de gekozen handeling lokaal uitvoeren terwijl waarneming beschikbaar blijft. Een antwoord krijgt een toestandsversie zodat een achterhaald antwoord niet op een andere track wordt toegepast. Jev kiest tracks en muzikale handelingen; lokale code leest standen, rekent timing uit en beweegt de bediening. Beat-uitlijning vóór faders blijft een uitvoeringsvoorwaarde. Codex neemt geen live DJ-keuzes over.

De wachtwoordvragen zijn een afzonderlijk lokaal probleem. De Bridge bewaart de sleutel na toegang in geheugen en geeft hem eenmaal via stdin aan de sessie. Er is geen Sleutelhanger-uitlezing per API-call. Herstarten wist die cache; opnieuw toestemming vragen hangt vervolgens van de Sleutelhangerrechten af. De code verklaart die mogelijkheid, maar bewijst niet welke eerdere dialoog precies zichtbaar was. Onderzoek vraagt geen nieuwe sleutel of wachtwoord en de Bridge is hiervoor niet herstart.

Conclusie: de autonome Jev-besliscyclus heeft concrete werkende voorbeelden en bruikbare openbare implementaties. De tekortkoming in de gemeten DJ-proef zit aantoonbaar in onze koppeling en de onvoltooide vervolgcyclus. De snelheid en betrouwbaarheid van een volledige Rekordbox-overgang blijven lokaal te valideren; ze volgen niet automatisch uit een gamevideo.
