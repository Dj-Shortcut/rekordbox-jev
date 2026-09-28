# Werk aan arrangement en inzetpunten — offline gecontroleerd

Verduidelijkte gebruikersafspraak: vanavond geen muziek of live mixproeven;
offline codecontroles mogen wel. Er is niets geïnstalleerd of live gestart.
De eerdere correctie van de abrupte tweede mix blijft lokaal behouden.

De volgende stap is lokaal aangesloten op de bestaande besliscontext:

- Bestaande viermaatsmetingen leveren hoogstens een **mogelijke eerste drop**.
  Een duidelijke stijging in energie en lage tonen naar de eerste aanhoudende
  actieve groep is een hypothese, geen herkende eerste kick of bewezen drop.
  Een actieve opening wordt niet vervangen door een latere terugkeer die dan
  ten onrechte “eerste drop” heet. Ontbrekend bewijs blijft onbekend.
- De aanloop van het inkomende nummer wordt omgerekend met zijn eigen tempo.
  Een gestopt nummer begint bij nul; een lopend nummer gebruikt zijn werkelijke
  positie. De uitgaande resterende tijd wordt apart meegenomen.
- Jev krijgt een expliciete vraag of opbouwen naar deze mogelijke drop bij beide
  arrangementen past. Alternatief en onbekend zijn volwaardige antwoorden.
  Te korte intro's, gepasseerde drops en een te late overdracht krijgen geen
  voorgeschreven intro/drop-recept. Dit geeft context, geen automatische cut,
  cue-sprong of gegarandeerde hoorbare basoverdracht.
- De widget heeft Nederlandse labels voor deze extra vraag.

De geschreven regressiegevallen zijn offline uitgevoerd; resultaten hieronder.
De nieuwe vraag kan echte beslislatentie beïnvloeden; dat is nog niet gemeten.

De gebruiker heeft de telling bevestigd na een voorbeeld uit map 26.
Het voorbeeld gebruikt Kotiēr — Smalltown Boy, het bestaande
beatgrid van 124,98 BPM en een bronfragment vanaf 2:45,485. De vermoedelijke
terugkeer op bronpositie 2:49,485 ligt daardoor op 0:04 in het fragment.
Bij acht beats per x volgen 0:07,841, 0:11,681 en 0:15,522.

## Lokaal aangesloten x-keuze

- X1 is de geschatte terugkeer op de uitgaande bron; iedere volgende x ligt
  acht beats/twee 4/4-maten verder. Lange uitgaande tracks hebben x1 als voorkeur,
  kortere x2. De bestaande grens van vijf minuten is hiervoor een expliciete
  voorlopige stijlgrens, geen uitspraak over de muzikale structuur.
- De aangeboden alternatieven zijn x2/x4/x6/x8, nooit x3/x5/x7. Alleen toekomstige
  punten met mengruimte worden aangeboden. Als de inkomende aanloop bij x2 te
  laat eindigt, kan een korte track toch x1 als voorkeur krijgen. Een te kort
  inkomend bestand krijgt geen fictief extra mengbudget.
- De nieuwe zelfstandige Jev-vraag `entry_slot` wordt alleen bij PLAY gebruikt.
  Een passende x wordt drie tot acht seconden vooraf aangevraagd. HOLD met een
  x-antwoord plant niets. Bij ontbrekende constante 4/4-grid, ongeschikte
  arrangementen of tijdnood blijft een terugvaloptie bestaan.
- De gekozen x wordt vertaald naar een vaste monotone tijd voor de inkomende
  downbeat. De native laag wacht tot die in beeld komt en zoekt uitsluitend de
  bijbehorende rode maatmarkering, binnen 180 ms van de bronklokschatting.
  Er wordt niet automatisch naar een andere maat verschoven. Vóór verzending
  worden trackidentiteit, gesloten route, afspelen, bronpositie en een verse
  maatmarkering opnieuw gecontroleerd. Een veranderd tijdstip van meer dan
  80 ms tussen twee native waarnemingen wordt afgewezen. Stop blijft actief.
- Zonder gekozen x blijft de oude inzet op een toekomstige zichtbare maatgrens
  bestaan. Bij een gekozen x is er één native poging; een gemist of onleesbaar
  punt vraagt een nieuwe Jev-keuze. Geen relatieve invoer wordt blind herhaald.
- De widget toont de voorkeur, de conditionele x-keuze en een mogelijke
  inkomende drop als schatting. Een x-antwoord bij HOLD blijft ongebruikt.

## Eerste hoorbare mengbeweging

Bij het verder nalopen van de uitvoering bleek dat een MIX-keuze met middenstand
en EQ-aanpassingen eerst een reeks EQ-stappen kon uitvoeren terwijl de inkomende
route nog gesloten was. Die extra wachttijd is lokaal aangepakt:

- Als Jev expliciet de middenstand kiest en het gesloten deck al met verlaagde
  bas en neutrale trim/mid/high klaarstaat, begint de uitvoering met die gekozen
  faderbeweging. De gekozen EQ-doelen volgen na bevestiging van de middenstand.
- De basvoorwaarde is een gemeten knopstand van maximaal −0,24; dit is geen
  dB-waarde. Onvoorbereide routes en eindoverdrachten behouden hun eerdere volgorde.
- De eerste middenstand wordt één keer gestuurd. Gekoppelde basstappen en het
  einde van dezelfde bundel sturen de fader daarna niet opnieuw.
- Een verse waarneming na de middenstand moet dezelfde tracks, uitlijning,
  afspelen, BPM en open kanalen bevestigen. Bij afwijkingen volgt geen EQ-reeks.
- De eerste bevestigde middenstand wordt als compacte `blend_confirmation`
  bewaard. De runner gebruikt die tijd voor de overlap zodra de hele bundel is
  geslaagd en de eindwaarneming nog een blend bevestigt. Latere EQ-stappen
  verplaatsen het begin van de gemeten overlap daardoor niet meer naar achteren.
  Ongeldige, te oude, toekomstige of bij andere tracks horende frames tellen niet.

De 68 gerichte adapter-/timingtests slagen. Daarin wordt beide richtingen getest,
evenals een mislukte bevestiging, veranderde uitlijning/track/fader/afspeelstand,
onvoorbereide EQ en ongeldige tijdmeting. Dit verwijdert een aantoonbare volgorde
van wachtwerk; het bewijst geen gemeten winst in seconden tijdens een echte mix.

## Bewijsgrens en volgende controle

Na de verduidelijking zijn de volgende offline controles uitgevoerd, zonder
Rekordbox-bediening, muziekweergave of echte Jev-aanvragen:

- Python 3.14.6: `cd demo && python3 -m unittest discover -s tests` rapporteert
  296 tests, OK, waarvan zeven overgeslagen wegens ontbrekende optionele
  Essentia/numpy-afhankelijkheden voor DSP-fixtures.
- Python 3.14.6: `python3 -m unittest discover -s tests` in de projectroot
  rapporteert 161 tests, OK, inclusief de Swift-markercontrole.
- De native tests zijn met `NATIVE_TRANSPORT_TESTS` tijdelijk gecompileerd en
  als `--demo-observer` uitgevoerd: **468 native transport safety cases PASS**.
  Alle invoer in dit testprogramma is gesimuleerd; geen echte bediening.
- De widget en bijbehorende controls slagen voor Swift-typecontrole.
  De native compilatie meldt bestaande deprecatiewaarschuwingen voor
  Sleutelhanger-API's; geen compilatiefouten.

Dit dekt onder meer lengtevoorkeur, toonhoogte/tempo, gemiste x2, te late drop,
gewijzigde bronpositie, klokparser en native afwijzing zonder verschuiving.
Er is geen app geïnstalleerd of gestart. Deze lokale Python-run bewijst geen
resultaat voor de afzonderlijke Python 3.11/3.12/3.13-jobs op GitHub.

De gekozen x plant **stille Play/downbeat**. De hoorbare faderinzet volgt op een
nieuwe waarneming, bevestigde uitlijning en een afzonderlijke MIX-keuze. De
broncode bewaart de geplande start en de vertraging tot de eerste bevestigde
blend apart (`confirmed_blend_after_entry_seconds`); bij een vroeg bevestigde
middenstand gebruikt die de waarneming vóór de verdere EQ-stappen. Het blijft
een conservatieve screenshotmeting, geen audiometing. Exact hoorbaar mengen op de
x of een perfect getimede basoverdracht op de drop is hiermee **niet bewezen**.
Het viermaatsgemiddelde waarmee x1 wordt voorgesteld kan bovendien vóór of na
de werkelijke eerste kick liggen. Dat blijft een punt voor de toegestane
luisterproef; een strakke uitvoering maakt een onnauwkeurig anker niet juist.

## CI en PR

De door de gebruiker genoemde CI is alleen-lezen bekeken op GitHub:
`.github/workflows/tests.yml` start bij zowel `push` als `pull_request`, met
Python 3.9–3.13 op Ubuntu en Python 3.12 plus systeem-Python/Swift op macOS.
Inmiddels is `origin/main` met de gemergede PR #5 (`11b63a5`) zonder conflicten
in de lokale werkbranch `codex/mix-entry-clock` opgenomen. Daarmee zijn Claudes
Python 3.9/3.10-timeoutcorrectie, timingfixtures en CI voor Python 3.9–3.13
behouden. De actieve suite slaagt daarna opnieuw onder Python 3.14.6 en Apple's
Python 3.9.6: beide 296 tests, OK, zeven optionele DSP-tests overgeslagen.

`tests/test_native_transport.py` sluit nu ook de volledige native harness aan op
unittest discovery. Op macOS compileert die de daadwerkelijke bridge met een
test-entrypoint en voert gesimuleerde bediening in de observer- en controlrol
uit. Een tweede test controleert de widgetcompilatie. Beide slagen lokaal.
De CI vereist expliciet dat marker-, transport- en widgetcontrole echt zijn
uitgevoerd; overslaan kan de macOS-job niet ten onrechte groen maken.
Op Linux worden alleen deze Mac-specifieke controles overgeslagen.

De eerste GitHub-run vond daarbij twee bestaande SwiftUI-tekstuitdrukkingen
die de compiler van de runner niet binnen zijn typecontrolebudget kon oplossen.
De buildregel en afspeelregel zijn opgesplitst in expliciete strings, met dezelfde
zichtbare tekst. Dit was lokaal niet zichtbaar en onderstreept waarom de nieuwe
widgetcontrole in CI nodig is.

De klokhelper waar Claude afzonderlijk aan werkt is niet in deze wijziging
opgenomen. PR #5 constateerde dat Apple's Python voor deadlinevergelijkingen
nog niet dezelfde klokbasis gebruikt als Swift. Geslaagde fixtures bewijzen
daarom nog geen werkende live set onder Apple's Python.
