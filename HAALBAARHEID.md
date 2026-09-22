> Historisch ontwikkelingsdocument. De actuele werking en proefresultaten staan in [demo/README.md](demo/README.md).

# DJ Jev — haalbaarheidsonderzoek

Datum: 21 september 2026. Onderzocht: bestaande broncode, bewaarde liveproeven en Playgroundresultaten, geïnstalleerde Rekordbox 6.8.7.0303, officiële Rekordbox- en TypeSafe-documentatie. Geen nieuwe DJ-sessie gestart, geen MIDI-mapping of bibliotheek gewijzigd, geen nieuwe API-aanvragen gedaan.

## Besluit

Een Jev-gestuurde DJ op deze Mac is technisch aannemelijk, maar het volledige gevraagde gedrag is nog niet aangetoond. De huidige combinatie van schermaflezing, muisbediening en een vaste overgang is geen voldoende betrouwbare basis om hem zelfstandig te laten doordraaien. Mijn eerdere werkwijze — een probleem aanpassen en onmiddellijk opnieuw live proberen — was daarvoor te vroeg.

Mijn technische voorkeur voor verder onderzoek is directe lokale bediening via MIDI, betrouwbare terugmelding van de decktoestand, en vooraf beschikbare muziekanalyse. Jev blijft de muzikale keuzes maken; lokale code voert die op tijd uit. Dat is een kandidaatarchitectuur, geen reeds werkende vervanging. Vooral de beschikbaarheid van een virtuele MIDI-route en de bruikbare terugmelding op deze specifieke Mac moeten nog worden vastgesteld.

## Wat ik concreet heb vastgesteld

| Onderdeel | Bevinding | Betekenis |
| --- | --- | --- |
| Jev-keuzes | De laatste liveproef bevat trackselectie en een overgangskeuze; de planvraag duurde ongeveer 0,66 seconde. | De cloudvraag was niet de geregistreerde blokkering van deze proef. Geen algemene maximumlatentie bewezen. |
| Lokale start | De log bevat een verzonden start met ongeveer 3,22 ms overschrijding van de lokale geplande verzendtijd. | Dit is geen meting van audiovertraging of de hoorbare beatfase. Daarna slaagden drie beeldcontroles. |
| Uitlijncontrole | De berekening kan een ontbrekende rode markering verwarren met een verschoven maat. | Een concrete fout in onze code; hieronder gereproduceerd zonder bediening. |
| Foutafhandeling | Een uitzondering verlaat de volledige sessielus. | Een tijdelijke onzekere meting kan doorlopend mixen beëindigen. Er is geen geteste opvang tegen stilte. |
| EQ | De app geeft gepaarde relatieve sleepbewegingen en controleert achteraf neutraliteit. | Twee even grote sleepbewegingen bewijzen geen constante gezamenlijke luidheid. |
| Muzikale informatie | De huidige sessie kent geen bevestigde frase-, drop- of vocalmarkeringen; het overgangsverloop is grotendeels vast. | Het programma voldoet nog niet aan zelfstandig muzikale overgangen kiezen zoals een mens. |
| Mac-rechten | De opgeslagen controles bevestigen rechten en sleuteltoegang na opnieuw ondertekenen/herstarten. | Dit praktische obstakel is afzonderlijk opgelost; het bewijst geen mixkwaliteit. |

De laatste liveproef staat in `evidence/sessions/d6adfde2-7521-40ed-9ddc-33ff917f56ba/session.json`: voorbereiding → uitgelijnde start → blokkering vóór de fade. Geen voltooide overgang en geen doorgang naar een derde track.

De oude Playground-CSV bevat 76 synthetische aanvragen, waarvan 61 voldeden aan de destijds verwachte uitkomst. De som van de weergegeven server- en netwerktijd had een mediaan van 329 ms en maximum van 1.403 ms. De prompts veranderden tijdens die reeks: 61/76 is daarom geen betrouwbare kwaliteitsmeting van de huidige versie. Het zijn ook geen live audioproeven of een volledige netwerkmeting.

## Reproduceerbare fout in de rode markeringen

`MixerVision.swift`, regels 39–47, neemt voor iedere A-markering de dichtstbijzijnde B-markering en gebruikt vervolgens de grootste afstand. Een bewaarde waarneming gaf 1 pixel verschil. Ik verwijderde uitsluitend één detectie uit de lijst van B-markeringen; de posities van alle overige markeringen bleven ongewijzigd. Dezelfde berekening rapporteerde vervolgens ongeveer 102 pixels verschil en zou de fade blokkeren.

Dit is een synthetische ontbrekende detectie op echte opgeslagen coördinaten. Er zijn geen screenshots aangepast. Het bewijst dat de controle deze fout kan maken. De exacte falende schermaflezing van de laatste liveproef is niet als aparte opname met meetwaarden vastgelegd, dus dit bewijst niet dat die fout toen de oorzaak was. Zie `research/marker-omission-check.json`.

Een goede vervanging moet zichtbaarheid, maatperiodiciteit en verschuiving apart beoordelen, echte scheefstand blijven blokkeren en een onzekere meting opnieuw controleren binnen een vooraf vastgelegde tijdsruimte. Alleen de tolerantie verruimen zou de oorzaak niet oplossen.

## Wat Jev kan bijdragen

Volgens de actuele [TypeSafe State-documentatie](https://docs.typesafe.ai/concepts/state) accepteert Jev tekst/JSON, geen audio, afbeeldingen of video. [De bouwrichtlijn](https://docs.typesafe.ai/concepts/how-to-build-with-system-one) beschrijft gerichte beslissingen in een workflow waarvan gewone code de uitvoering beheert.

Daaruit volgt voor dit project: betrouwbare informatie over intro/outro, vocals, energie, beatgrid en mogelijke overgangspunten moet vóór de vragen beschikbaar zijn. Tutorialtekst geeft regels, maar vertelt niet waar de vocal of drop in een specifieke track zit. Jev kan vervolgens onderbouwd kiezen tussen geschikte tracks, instappunten en mixhandelingen. Meer vragen stellen vervangt ontbrekende waarneming niet. De huidige twee vragen — track en duur — zijn te beperkt voor de gevraagde muzikale vrijheid.

## Bedieningsroutes voor Rekordbox

| Route | Onderbouwing | Beoordeling |
| --- | --- | --- |
| Bestaande scherm- en muisbediening | We hebben echte bediening gezien; de code eist een vaste vensterindeling en Rekordbox op de voorgrond. | Bruikbaar voor beperkte experimenten. De huidige betrouwbaarheid voldoet niet. De ene muis voert EQ-gebaren achter elkaar uit. |
| MIDI voor transport, faders en EQ | De geïnstalleerde DDJ-400-mapping bevat PlayPause, Sync, CrossFader, ChannelFader en EQLow. De officiële Rekordbox-6-FAQ bevestigt MIDI Learn via Hardware Unlock. | Meest kansrijke uitvoerroute: meerdere bedieningselementen met korte berichten aansturen, zonder per beweging de muis te verplaatsen. Ontvangst, resolutie, terugmelding en timing moeten lokaal worden aangetoond. |
| Rekordbox Automix / Mix Point Link | De officiële 6.8-handleiding beschrijft Automix; de FAQ beschrijft voorbereide automatische starts met Mix Point Link. | Bestaande interne functies verdienen onderzoek, maar geven niet vanzelf Jev zeggenschap over iedere EQ- of faderkeuze. Niet stilzwijgend als vervanging inzetten. |
| Externe open-source bridges | CLI-Anything documenteert virtuele MIDI; rekordbox-control-cpp documenteert WinMM-bediening. | Bruikbare onderzoeksaanknopingspunten, geen geverifieerde kant-en-klare oplossing voor deze Mac. Niet geïnstalleerd. |

MIDI is niet hetzelfde als onbeperkte toegang tot elke Rekordbox-functie of exacte live deckpositie. De geraadpleegde mappings geven bijvoorbeeld Play/Sync-indicatoren terug, maar bewijzen geen algemene terugmelding van alle EQ-standen en beatposities. Een verzonden bericht is geen bevestigde bediening.

Volgens de [officiële Rekordbox-6-FAQ](https://rekordbox.com/en/support/faq/v6/#faq-q1525) ontgrendelen geschikte apparaten, waaronder DDJ-1000 en DDJ-400, Performance-bediening en MIDI Learn zonder betaald abonnement. Dat maakt de route afhankelijk van de werkelijk beschikbare hardware/licentie. De read-only Bridge-status tijdens dit onderzoek meldde geen MIDI-bronnen; Rekordbox was toen gesloten. Er is niets opnieuw gestart of aangesloten. Dit zegt niet welke hardware je bezit.

De geïnstalleerde DDJ-1000-mapping bevat bovendien andere mixerregels dan DDJ-400, waaronder gemarkeerde faderregels. Daarom mag ik geen hardwaremapping blind kopiëren of een controller nabootsen om beperkingen te omzeilen.

[Mix Point Link](https://rekordbox.com/en/support/faq/rekordbox6/#faq-46548) kan een vooraf ingestelde start intern laten uitvoeren. De [licentievoorwaarden voor bediening daarvan](https://rekordbox.com/en/support/faq/rekordbox6/#faq-46534) verschillen van gewone MIDI Learn: de FAQ beperkt de muisproef tot gebruik zonder MIDI/HID-apparaat en noemt een passend plan voor MIDI/HID of sneltoetsen. Het is dus geen gegarandeerde gratis oplossing.

[Ableton Link](https://help.ableton.com/hc/en-us/articles/209776125-Link-features-and-functions-FAQ) kan muzikale beat, tempo en fase delen. Dat levert op zichzelf nog geen volledige Rekordbox-decktoestand, trackselectie of EQ-bediening. PRO DJ Link-bibliotheken zijn voornamelijk op DJ-apparatuur gericht en zijn niet zonder verificatie een status-API voor de twee softwaredecks.

## Beslispunt vóór verder bouwen

Eerst is één afgebakende technische proef nodig die losstaat van een DJ-demo: werkt een toegestane directe bedieningsroute op deze setup, en kan de app het resultaat betrouwbaar waarnemen? Daarbij moet ze twee EQ-kanalen in samenhang kunnen sturen, herhaaldelijk de bedoelde eindstanden bevestigen en uitlijning controleren zonder dat een ontbrekende detectie als scheefstand wordt behandeld. Licentie of hardware mag niet worden verondersteld.

Daarna moeten opgenomen toestanden de volledige beslis- en herstelketen doorlopen: late/ontbrekende Jev-antwoorden, een gemiste markering, laadfouten en een korte onderbreking van de waarneming. Jev-keuzes worden vóór hun uitvoermoment klaargezet. De app moet een volgende track ruim vóór het einde gereed hebben en mag een tijdelijke meetfout niet automatisch tot sessie-einde maken. Dit bewijst nog geen hoorbare kwaliteit, maar voorkomt dat een live demo de eerste integratiecontrole is.

Een volgende muzikale proef telt pas als resultaat wanneer minstens drie tracks achtereen worden afgewerkt, de fasecontrole tijdens alle overgangen werkt, EQ na afloop neutraal is en de uitvoeraudio op onbedoelde stiltes en niveausprongen is gecontroleerd. De registratie moet per blokkering het beeld, de meetwaarden en de beslissing bewaren. Een absolute belofte dat er onder alle omstandigheden nooit stilte kan zijn is niet onderbouwd; de huidige code heeft zelfs nog geen geteste foutopvang.

Mijn onderzoeksadvies: geen verdere live reparatieproeven op de huidige GUI-lus. Eerst de bedienings- en waarnemingsroute kwalificeren. Als die onder de bestaande Mac/Rekordbox/licentievoorwaarden onvoldoende blijkt, moet ik dat als grens melden voordat er meer aan widget of mixlogica wordt gebouwd.

## Bronnen en lokaal bewijs

- [Rekordbox 6.8-handleiding](https://cdn.rekordbox.com/files/20231201105226/rekordbox6.8.0_manual_EN.pdf): pagina 145 Beat Sync, pagina 149 Automix.
- [Rekordbox 6 Hardware Unlock](https://rekordbox.com/en/support/faq/v6/#faq-q1525).
- [TypeSafe State](https://docs.typesafe.ai/concepts/state) en [bouwrichtlijn](https://docs.typesafe.ai/concepts/how-to-build-with-system-one); rechtstreeks van docs.typesafe.ai gelezen.
- [CLI-Anything, eigen projectbeschrijving](https://github.com/HKUDS/CLI-Anything/blob/main/rekordbox/agent-harness/REKORDBOX.md).
- [rekordbox-control-cpp, eigen projectbeschrijving](https://github.com/Moonwolf711/rekordbox-control-cpp).
- Lokale broncode: `Sources/MixerVision.swift`, `Sources/Bridge.swift`, `scripts/controller.py`, `scripts/dj_session.py`, `scripts/music_context.py`.
- Lokaal bewijs: genoemde sessielog, `evidence/alignment-live-samples.json`, `evidence/persistent-access-check.json`, `../jev-playground-prototype/results.csv` en bijbehorende README.
