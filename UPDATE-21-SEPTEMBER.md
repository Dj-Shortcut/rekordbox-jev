# DJ Jev — stand na de aanpassingen

De zwevende widget heet **DJ Jev**. Hij toont vraag- en antwoordkaarten, de gekozen antwoorden en kansbalkjes. Start/stop, invoervelden, tabbladen en andere bediening staan niet in dit venster. Het tijdstip bovenaan hoort bij de getoonde aanvraag: historische antwoorden zijn geen lopende sessie.

De widget leest uitsluitend de lokale weergavekopie onder `/private/tmp/rekordbox-bridge-<uid>/widget/`. Hij maakt geen `JevControls` aan, benadert geen Sleutelhanger, vraagt geen toegang tot Documenten en bedient Rekordbox niet. De oorspronkelijke aanvraaglogs blijven in `evidence/jev-events`; de schrijver werkt de weergavekopie automatisch bij. De cache is vluchtig.

## Mixbediening: protocol 2 actief, live mixverificatie nog open

- Rode maatmarkeringen moeten zichtbaar gelijk zijn vóór iedere faderstap. Onbekend, scheef of te oud blokkeert de stap, inclusief noodsprongen. De Swift-uitvoerder en Python-uitvoerder controleren dit allebei.
- EQ-terugstelling gebruikt een dubbele klik en controle van de neutrale wijzerstand. Een tegengestelde sleepactie telt niet als bevestiging.
- Een bass-overdracht gebruikt kleine gekoppelde stappen voor beide decks in één native opdracht. De GUI heeft één muiswijzer: de twee gebaren volgen kort na elkaar en zijn niet letterlijk gelijktijdig. Er zit geen Jev-aanvraag tussen.
- Jev kiest het volgende nummer en de beschikbare overgangslengte vooraf. De lokale startplanner gebruikt zichtbare maatmarkeringen en de leeftijd van de waarneming. Een gemiste startdeadline wordt afgewezen, niet laat uitgevoerd.
- De sessiecode wisselt de deckrollen na iedere overgang en bereidt daarna het volgende nummer uit map 26 voor. Er is geen stop na één nummer.
- Tutorialinstructies worden volledig aan de planvraag meegegeven; onbekende frases/drops worden niet verzonnen.

Het ongeveer constant houden van de daadwerkelijke uitgangsluidheid is **nog niet gemeten of geregeld**. Tegengestelde EQ-bewegingen alleen bewijzen dat niet. Ook het doorlopend laden, de nieuwe lokale startplanning en meerdere aansluitende overgangen hebben nog geen geslaagde live proef.

## Controle en concrete blokkering

47 Python-controles slagen, waaronder geen fader bij scheve/ontbrekende rode markeringen, stoppen bij uitlijnverlies tijdens een fade, geen grote inhaalbeweging bij een gemiste deadline, een EQ-eindstand die niet neutraal is afwijzen, en doorgaan naar een derde track na twee gesimuleerde overgangen. De nieuwe native Bridge en de widget compileren. Op een echte schermaflezing herkent de beeldlezer een verschil van 14,5 pixels en onderscheidt hij de neutrale bass van deck A van de verlaagde bass van deck B.

De actieve Bridge gebruikt protocol 2 en een vaste lokale certificaatgebonden ondertekening. De oude macOS-toestemmingsregistratie is alleen voor deze app vernieuwd. Toegankelijkheid en schermopname zijn beide actief. Vervolgens is de appversie gewijzigd, opnieuw ondertekend en herstart: beide rechten bleven actief zonder nieuwe wachtwoordvraag. De controles staan in `evidence/permissions-before-update.json` en `evidence/permissions-after-update.json`.

De widget en Bridge gebruiken voortaan `scripts/sign_app.py`. Deze weigert terug te vallen op ad-hoc ondertekening. De bestaande identiteit wordt hergebruikt; er wordt geen nieuwe privésleutel bij elke build gemaakt. De Bridge gaat de bestaande TypeSafe-sleutel via macOS Sleutelhanger gebruiken en geeft haar alleen via een anonieme stdin-pipe aan het sessieproces. De widget leest uitsluitend vraag- en antwoordgebeurtenissen. Na de bevestiging is de Bridge opnieuw gebouwd, ondertekend en herstart. `djReady` kon daarna de bestaande sleutel lezen met macOS-interactie uitgeschakeld. Ook beide bedieningsrechten bleven actief. Resultaat: `evidence/persistent-access-check.json`. De sleutel zelf staat niet in dit bewijsbestand.

Er is geen nieuwe API-sleutel gevraagd. De sleutel wordt niet in broncode, argumenten, omgevingsvariabelen of logs opgeslagen. De laatste volledige live overgang was niet succesvol. Er is nu geen automatische DJ-sessie actief. De UI is wel geopend en visueel gecontroleerd.
