> Laatste lokale wijzigingen, avond 27 september: de [abrupte tweede overgang](VIDEO-REVIEW-2026-09-27.md) is onderzocht en de [x-telling, gekozen inzet en mogelijke inkomende drop](ARRANGEMENT-WORK-2026-09-27.md) zijn uitgewerkt. X-punten liggen acht beats uit elkaar; lange tracks krijgen x1 als voorkeur, kortere x2, tenzij arrangement of mengtijd een alternatief vraagt. **Offline codecontroles geslaagd; zeven optionele DSP-tests overgeslagen. Geen muziek, live mixproef of installatie uitgevoerd.** De hoorbare verbetering is nog niet bewezen. Wijzigingen blijven lokaal, zonder push of PR.

De eerste gekozen middenstand wordt nu vóór de EQ-reeks uitgevoerd wanneer het
inkomende deck al gesloten en met verlaagde bas klaarstaat. De overlapteller
gebruikt de bevestiging van die middenstand, zodat latere EQ-stappen niet als
extra wachttijd vóór de blend worden gerekend. Zie het gekoppelde controleverslag.

> Stabiliteitsupdate voor issue #2: begrensd Bridge-/focusherstel, sessiebewaking, crash-evidence en één gecontroleerde build/startflow. Zie [de herstelgrenzen en testinstructies](STABILITY.md). Deze update is offline getest; een nieuwe langere live proef staat nog open.

## Herstel en validatie van 27 september

De herstelde geïnstalleerde versie heeft nu twee volledige autonome cycli doorlopen. Run `8863967e-b1d5-4fa6-8e2b-6d7d67f08d9d` ging van Attract naar UH HUH naar Acido en zette daarna I Feel Love klaar. Beide overdrachten waren meer dan 22 seconden vóór het einde afgerond. De eerdere laadfout is in deze cyclus niet teruggekomen; lees [het verificatieverslag](VALIDATION-2026-09-27.md) voor de exacte bewijsgrenzen. Het afsluiten van een stilstaand deck leest nu alleen de benodigde routepixels en bewaart tijd voor de invoercontrole. Een afgelopen track met een groen afspeellicht wordt als een stilstaande bronklok herkend. Een afgeronde maar onverwachte EQ-beweging mag uitsluitend bij volledig leesbare tracks, playback, uitlijning en mixerstanden twee nieuwe stabiele waarnemingen krijgen; daarna is een nieuwe Jev-keuze nodig. De relatieve beweging wordt nooit blind herhaald. Onbekende invoer, gewijzigde tracks of blijvende afwijkingen blijven een blokkade.

Bij een bevestigde lopende overgang krijgen de laatste 30 seconden van de uitgaande track voorrang: Jev krijgt de bestaande overgang afronden als transportoptie, de inkomende faderzijde als doel en EQ vasthouden. De lokale avondcorrectie biedt daarbij 8 of 16 beats zolang beide klokken na de beweging minstens 12 seconden overhouden; anders blijven 2 of 4 beats beschikbaar. Hierna wordt eerst de hoorbare opvolger geneutraliseerd en het oude deck gestopt, voor de volgende voorbereiding. Een nog lopende EQ-reeks geeft bij deze tijdgrens na een afgeronde, leesbare beweging de controle terug voor een nieuwe Jev-keuze. Dit start geen gestopt deck. Alleen binnen herkenbare klokwaarden wordt de OCR-verwisseling van de laatste nul met `o`/`о` gecorrigeerd.

209 Python-controles: 202 geslaagd, 7 optionele DSP-controles overgeslagen. 444 native controles geslaagd. Dit zijn technische controles; het afzonderlijke live bewijs staat in het verificatieverslag.

# DJ Jev — lokaal prototype

Doel: met één Start-knop een doorgaande set draaien in Rekordbox, met Jev als beslisser. **De laatste beoordeelde live run heeft drie opeenvolgende overdrachten inclusief EQ-herstel uitgevoerd en bleef verder draaien.** Dat bewijst de functionele keten op dat meetmoment; het is geen garantie voor iedere volgende overgang of een muzikaal afgewerkte DJ-set.

Nieuwe optionele uitbreiding: [offline Essentia-tijdlijnen voor beide decks](AUDIO-TRANSITIONS.md). Die geven Jev bronenergie, bass-inhoud, aanvalsdichtheid en spectrale verandering rond de afspeelposities. De analyse draait vooraf; de bestaande bediening blijft begrensd. De technische metingen en tests zijn gecontroleerd, een muzikaal betere live mix is hiermee nog niet aangetoond.

## Welke delen wat doen

Sinds 26 september voegt `djjev/dj_context.py` een situatiegebonden DJ-checklist toe aan de echte Jev-aanvraag: openen, selecteren/voorbereiden, wachten op inzet, uitlijnen, overlappen of opruimen. Genre uit de Rekordbox-export gaat mee voor kandidaten en geladen decks; onbekende genres, zang, secties en publieksreacties worden niet ingevuld. De kern veronderstelt niet meer dat ieder nummer uit map 26 EDM is. De bestaande legale keuzes, timingvoorkeuren, parametervertaling en fysieke controles blijven intact. De checklist is context, geen achttien aparte antwoorden of vaste mixprocedure.

### Herstel na een onzekere bediening

Wanneer een niet-mixactie geen bevestigde teruglezing krijgt, wordt de oude beslissing niet opnieuw verstuurd. De runner gaat tijdelijk naar `execution_reconciling`, leest Rekordbox opnieuw en wacht op twee stabiele toestanden voordat hij een nieuwe Jev-vraag toestaat. Na drie opeenvolgende onzekere bedieningen blijft de sessie uit veiligheid geblokkeerd. Een gedeeltelijk uitgevoerde `mixGesture` blijft strenger: alleen het bestaande beperkte mixerherstel mag helpen; anders wordt de mix niet herhaald. Zo kan een tijdelijke Bridge- of terugleesfout zichzelf herstellen zonder van Codex afhankelijk te worden, terwijl een onbekende faderbeweging nooit blind wordt overgedaan.

Herstel vereist twee opeenvolgende leesbare waarnemingen met dezelfde tracks, playback, sync/master, uitlijning, kanaalfaders en EQ; kleine numerieke meetruis is toegestaan. Een onleesbare of mislukte meting verbreekt de reeks. HOLD wist de foutenteller niet. Een waargenomen start van een stille opvolger kan de overgangscontext herstellen, zonder de oorspronkelijke bediening als bevestigd te tellen. Bij een onzekere echo geldt conservatief de cooldown van 45 seconden. Een nog lopende verouderde Jev-aanvraag wordt geannuleerd voordat een nieuwe beslissing wordt gevraagd.

Alle achttien menselijke DJ-vragen zijn uitgewerkt met informatiebehoefte, antwoordmogelijkheden en implementatiestatus. Zelfstandige setrichting, vrije cuekeuze, cuts/loops/effecten en het beoordelen van live mixaudio zijn nog niet geïmplementeerd. `python3 scripts/inspect_dj_context.py --output-dir <map>` exporteert dit contract en controleert de lokale metadata en bestaande audiocaches zonder Jev-aanvraag of Rekordbox-bediening. De contextuitbreiding is offline getest, nog niet met een nieuwe live set beoordeeld.

- `djjev/runner.py` laat waarneming, maximaal één Jev-aanvraag en maximaal één bediening onafhankelijk lopen. Lezen en de UI gaan door tijdens aanvragen en laadhandelingen. Verouderde antwoorden worden verworpen; na bediening is een nieuwe waarneming nodig.
- `djjev/policy.py` biedt keuzes op basis van de actuele toestand. Jev kiest opvolger, inzet en mixerdoelen. Code controleert onder meer map 26, trackidentiteit, key/tempo, routes en rode-beatuitlijning en geeft stille voorbereiding voorrang op wachten. Er is geen vaste nummerlijst of vaste muzikale mixprocedure.
- `djjev/environment.py` vertaalt gekozen acties naar native bediening en controleert de zichtbare uitkomst. Nieuwe screenshots bevestigen veranderingen zonder dezelfde muisactie blind te herhalen. Onbekende of gedeeltelijk uitgevoerde fouten blokkeren verdere bediening.
- `djjev/jev_client.py` gebruikt `jev-latest` via een blijvende HTTPS-verbinding. De bestaande hoofd-Bridge geeft de reeds toegestane sleutel eenmaal aan de sessie; native lees- en bedieningsprocessen krijgen die sleutel niet. Codex maakt tijdens de lus geen DJ-keuzes.

De gelijktijdige taken volgen het idee uit de [openbare Doom-implementatie van AmoghCreator](https://github.com/AmoghCreator/doom-jev/blob/main/main.py). Deze Python-kern importeert geen oude DJ-planner of sessielus.

## Antwoord, uitvoering en bevestiging

De widget 0.5 toont tijdens de set automatisch de laatst volledig beantwoorde aanvraag, met alle gelijktijdige vragen en antwoorden. Een volgende lopende aanvraag verbergt die antwoorden niet. Het venster is verstelbaar, bewaart zijn afmetingen en laat tekst doorlopen; bij meer breedte verschijnen de kaarten naast elkaar. De standaardkaart bevat alleen vraag, antwoord en een uitklapbare Details-sectie met volledige instructies en alle antwoordopties/kansen.

Beeld pauzeren houdt uitsluitend de geselecteerde aanvraag vast; de set blijft lopen. Live volgen hervat de actuele antwoorden. Geschiedenis, Context, DJ-checklist en Alles tonen respectievelijk eerdere aanvragen, de aangeleverde waarnemingen, de achttien beoordelingsvragen en de onverkorte aanvraag/antwoordgegevens. De checklist pretendeert geen achttien afzonderlijke Jev-antwoorden. Uitvoeringsstatus wordt per aanvraag opgeslagen, zodat een later setbericht niet als bevestiging van een oudere keuze verschijnt. Oude records zonder deze terugkoppeling tonen geen verzonnen uitvoering. De interface en eventkoppeling zijn getest; er is voor deze UI-wijziging geen set gestart.

`transport=mix` kiest expliciet een mixerbeweging. Afzonderlijke fader-, bass- en duurvragen veronderstellen **“als MIX gekozen wordt”**; alleen bij `mix` worden hun antwoorden gebruikt. Een trackantwoord wordt alleen gebruikt bij `load_A` of `load_B`. De widget bewaart de echte antwoorden en kansen, maar toont ongebruikte takken gedimd als **“Niet uitgevoerd · Jev koos …”**, onder **“Bij mengen”** of **“Bij laden: welk nummer?”**. Een modelantwoord is geen uitvoeringsbevestiging.

Bevestigde betekenisvolle acties en recent geladen tracks blijven apart van HOLD bewaard. Na een bevestigde start van een onhoorbare opvolger onthoudt de runner het inkomende en uitgaande deck en beide trackidentiteiten. Die context overleeft HOLD-keuzes en vervalt bij andere trackidentiteiten. Jev krijgt resterende tijd, fader-eindpunt en EQ-herstel mee om zelf verder te kiezen.

Zijn beide decks bevestigd gestopt, dan kan de app de route naar het door Jev gekozen geladen deck openen en dat starten; er is geen vast openingsdeck. Gewone mixbewegingen vereisen uitgelijnde rode markers. Fader en bass kunnen binnen één gekozen beweging veranderen; de ene muiscursor voert de kleine acties lokaal na elkaar uit.

Trackherkenning probeert eerst een exacte titel. Een afgekorte titel mag alleen via een uniek letterlijk begin van minstens 32 tekens aan één bibliotheektrack worden gekoppeld. De oorspronkelijke waargenomen titel blijft bewaard; de herkenning vult geen gegokte woorden aan.

De native laag plant Play lokaal op een toekomstige maatgrens. Voor een expliciet door Jev gekozen x wordt uitsluitend die gekozen maat geaccepteerd, na controle van de bronklok en zichtbare markeringen. Een gemist punt wordt niet naar een andere maat doorgeschoven. Zonder expliciete x mag de bestaande terugval maximaal vier keer opnieuw waarnemen en een toekomstige maatgrens plannen zolang nog geen Play verstuurd is. Een reeds verstuurde Play wordt niet herhaald. Geplande en werkelijke verzendtijden worden afzonderlijk gelogd. Stille Play en later hoorbaar mengen blijven verschillende gebeurtenissen.

## Rustiger muzikale timing

Voorbereiden en inzetten zijn gescheiden. Zodra één track hoorbaar speelt en het andere deck stilstaat, beperkt code de eerstvolgende aanvraag tot het beschikbare voorbereidende werk: noodzakelijke EQ-afwerking, een verse opvolger laden of de gekozen opvolger stil klaarzetten. Dit hangt niet af van het mixvenster, de kickanalyse of de resterende speelduur. Jev kiest de track uit de geschikte kandidaten; antwoorden worden niet achteraf herschreven. Is er geen geschikte kandidaat of geen veilige voorbereidende actie, dan blijft dat expliciet `preparation.status=unavailable`; er wordt geen track verzonnen.

Zodra de opvolger gereed is, blijven wachten en inzetten afzonderlijke muzikale keuzes. De klaarstaande track wordt tijdens het wachten niet opnieuw vervangen of van zijn voorbereide bassinstelling ontdaan. De huidige track, zeker de opener, mag eerst zijn ontwikkeling en sterke passage brengen. Een rustige intro, korte energiedip of klaarstaand deck is op zichzelf geen aanleiding om te starten. Herhaald HOLD is dan gewenst. Tijdens noodzakelijke voorbereiding vervallen de vragen over het mixmoment; die verschijnen weer zodra ze relevant zijn.

Deze scheiding is getest met korte en lange tracks, een late kicksectie bij een track van zeven minuten, veranderde gereedheid, ontbrekende kandidaten en de productie-runner met een expliciete provider/native-fixture. De fixture laadt en bereidt vroeg voor zonder de opvolger te starten. Dit is geen bewijs van een volledige echte Rekordbox-overgang.

Voor korte tracks geldt ongeveer **32 maten** als uitgangspunt; wanneer beide bekende trackduren minstens vijf minuten zijn **64 maten**, met ruimte om langer in elkaar te vloeien als beide passages dat dragen. Een korter alternatief is respectievelijk 16 of 32 maten. Tracks tussen drie en vijf minuten behouden 32 als neutraal uitgangspunt; Jev beoordeelt de vorm en beschikbare ruimte. Het geschatte instapvenster omvat de voorkeursduur plus 16 maten voorbereiding, **begrensd tot het laatste kwart van de track wanneer de duur bekend is**. De muzikale ontwikkeling gaat voor: een kortere overlap heeft de voorkeur boven eerder starten om 64 maten te halen. Een passend gridpunt opent het venster niet eerder. Dit zijn stijlvoorkeuren in `djjev/musical_timing.py` en `djjev/policy.py`, geen actieblokkades of verplichte lengtes. Een naderend einde krijgt voorrang. De kortste bekende track bepaalt de voorkeur; bij een opvolger van drie minuten blijft het dus een kortere mix. Tijdens overlap begrenst de kleinste resterende klok van de spelende decks de beschikbare tijd, met dezelfde dertig seconden reserve als de afrondcontrole. Vóór de start krijgt een kandidaat daarnaast acht seconden geschatte start- en uitlijnruimte; een te krappe laatste sectie schuift het terugvalvenster niet op.

Deze correctie volgt op live aanvraag 83 van `57da304c-ed39-4039-a319-ee52a0d3cab2`: de opvolger startte bij 145,2 seconden van 300,4 seconden, zonder aangeleverde audiostructuur. Het oude venster stond toen al open. Met de correctie blijven op datzelfde meetpunt nog 80,1 seconden tot het voorkeursvenster over. De 75%-grens is een conservatieve stijlkeuze, geen bewijs dat een hoogtepunt of frase is gepasseerd; een betere hoorbare uitkomst moet nog live worden beoordeeld.

Bij beschikbare audioanalyse ziet Jev ook het geordende bronverloop over de volledige track. Dit helpt eerdere, huidige en komende sterke passages te vergelijken. Het is geen detector die een hoogtepunt bewijst of zegt wat de luisteraar al hoorde. Bij onvoldoende bewijs blijft het late tijdvenster de terugvaloptie. De lokale audio-informatie mag de wens om het nummer te laten ontwikkelen niet overrulen.

De runner onthoudt wanneer beide decks bevestigd hoorbaar gemengd stonden. Jev ziet de verstreken overlap en kan de blend langer aanhouden met HOLD tussen korte fader-/bassbewegingen. Het aantal antwoorden of de eerder onhoorbare start telt niet als mixtijd. Pauzeren, de route sluiten, seeken, opnieuw uitlijnen of andere tracks laden maakt die tijdregistratie ongeldig. Afzonderlijke native bewegingen en de rode-markerbeveiliging zijn ongewijzigd.

Naast de oudere **geschatte groepering van 16 maten** krijgt Jev bij gevalideerde analyse nu x-punten van acht beats, geankerd aan een mogelijke laatste terugkeer. De klokberekening houdt rekening met bron- en livetempo. Deze punten zijn geen bewezen frases, drops of kicks. Bij ontbrekend of variabel grid blijft de bestaande terugval op resterende tijd en zichtbare maatgrenzen bestaan. Zie het avondverslag voor het verschil tussen een gekozen startpunt en een bewezen hoorbare inzet.

De eerste live Essentia-proef (`776e5dca`) koos de opvolger al bij 14,5 seconden en liep later vast in een gepaarde bassbeweging. Dat was geen geslaagde overgang. De daarop aangescherpte ontwikkeling- en duurvoorkeuren zijn nog niet in een nieuwe hoorbare set beoordeeld. Het geslaagde live bewijs hieronder betreft de eerdere snellere spelvoorkeuren.

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

## Verbindingsherstel — 26 september 2026

De lokale Bridge werd tijdens vertraagde aanvragen door macOS met signaal 13 (SIGPIPE) beëindigd. De host negeert nu SIGPIPE op procesniveau, zodat een verdwenen lezer als schrijffout wordt afgehandeld. De bestaande socketbeveiliging en fysieke controles blijven behouden. Widget 0.5.1 wist een oude verbindingsmelding na een geslaagde statuscontrole, terwijl sleutel- en startfouten zichtbaar blijven.

Gecontroleerd: 252 native veiligheidscontroles, 166 Python-tests van de bridge, beide widgetcontroles en vijf live afgebroken alleen-lezen aanvragen met hetzelfde hostproces. Na herstel van de vensterverhouding en normale Sleutelhanger-toestemming zijn `play_A`, `load_B` en `prepare_B` live bevestigd. Dat bewijst herstel van de start- en voorbereidingsketen; een volledige overgang is in deze herstelproef nog niet bevestigd.

## Meer ruimte voor EQ-keuzes

Jev kan zelf lichte, middelsterke of diepe basoverdracht kiezen, of de bestaande basbalans laten staan. Midden en hoog zijn afzonderlijke MIX-vragen: licht of duidelijk reduceren op A of B, neutraal herstellen, of behouden. Dit is een palet, geen verplichte reeks. Alle doelen blijven onder of op neutraal; trim blijft ongemoeid. De waarden zijn gemeten knophoeken, geen dB.

De instructies vragen om passagegebonden keuzes en rust tussen ingrepen. Ontbrekende bronanalyse, zangherkenning en live mixaudio blijven expliciet onbekend. Een genrelabel bewijst geen klank. Elk gestuurd stapje wordt teruggelezen; onleesbare EQ of verloren uitlijning stopt de bediening. Bereikte doelen worden niet opnieuw aangeboden. De widget toont de afzonderlijke bas-, midden- en hoogkeuzes, en vermeldt of de MIX-tak werkelijk werd uitgevoerd.

Regressiecontrole gebeurt met de echte beslislus en gesimuleerde bedieningsresultaten. Dit bewijst de verbinding tussen keuzes en bediening, niet dat Jev al creatief of goed hoorbaar mixt; daarvoor blijft een live luisterproef nodig.


### Final sustained return preference (folder 26)

Entry timing now considers the user's `xxx____xxxxxx_____xxxxxx` pattern: prefer
introducing the successor at the last suitable sustained kick return after the
main high point. This can keep seven-minute tracks playing longer than the old
75-percent fallback. It is a preference, not a mandatory structure for all songs.

`entry_timing.py` derives candidate groups once at startup from validated cached
four-bar measurements. A group needs at least 16 bars of low-band energy and
attack activity; a return also needs a previous sustained group and a measured
bass drop/rise. This is **not an instrument or climax detector**. The entire file
is checked; missing analysis and ambiguous patterns stay explicit. No decoder
or DSP runs inside the live observation loop.

Four independent Jev Choice questions assess the pattern, proposed final group,
post-peak interpretation and entry suitability. Transport remains the action
choice. Contradictory non-urgent PLAY answers are rejected, never replaced with
fabricated model answers. Unknown/rejected candidates retain the conservative
late-track fallback; urgent continuity takes priority. Computed wait times and
mix budgets respect source/live tempo, the candidate section and both tracks.
The selected point is a four-bar estimate, not a guaranteed phrase boundary.

The live widget shows these real answers plus the proposed timestamp and mix
budget. Version 0.5.3. Recording and effects were not modified for this change.
Validation includes synthetic structure/clock cases and the local folder-26
cache. Musical correctness and API latency with these extra questions still
require a live listening trial; passing code tests does not establish either.

## Zelfstandige Start en mixerherstel

Start opent de lokale Bridge en Rekordbox indien nodig, gebruikt de bestaande
Sleutelhanger-toestemming, ververst de map-26-inventaris en vraagt Rekordbox via
zijn normale menu om een actuele XML-export. Alleen bestanden binnen map 26
komen in de set. Nieuwe bestanden worden zo nodig via Map importeren toegevoegd.
Ontbrekende bronanalyses worden door de meegeleverde analyseruntime voorbereid;
geldige caches worden hergebruikt. Het widget toont Voorbereiden tot dit klaar is.
De runtime gebruikt zijn eigen gecontroleerde export, niet een handmatig bestand
op het bureaublad. Opnames, effecten en audio-instellingen horen niet bij Start.

Een mixergebaar sleept de crossfader vanaf de gemeten stand. Kleine EQ-bewegingen
worden niet meer verder opgeknipt door de faderinterpolatie. Een ontvangen maar
onvolledig bereikt mixdoel kan maximaal tweemaal opnieuw beoordeeld worden, mits
alle bedieningsstappen afgerond zijn en tracks, afspelen, uitlijning en mixerstand
leesbaar blijven. Twee nieuwe stabiele waarnemingen zijn verplicht. Daarna komt
een nieuwe Jev-beslissing; relatieve invoer wordt nooit blind herhaald en een
onvolledige beweging wordt niet als geslaagd geboekt. Onbekende uitvoering,
gewijzigde tracks, onleesbare standen en aanhoudend uitblijvende reactie blijven
zichtbaar onderbroken. Dit is geen garantie tegen fysieke of systeemstoringen.
