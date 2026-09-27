# Tweede overgang uit de opname van 20:00:51

Status: bestaande opname en sessielog onderzocht; lokale correctie geschreven.
De gebruiker verduidelijkte dat alleen muziek en live mixproeven vanavond niet
gewenst zijn. Offline codecontroles zijn daarna geslaagd; zie het
[controleverslag](ARRANGEMENT-WORK-2026-09-27.md). De correctie is niet
geïnstalleerd en er is geen nieuwe Rekordbox-sessie gestart.

## Bewijs

Opname: `Schermopname 2026-09-27 om 20.00.51.mov`, duur 14:59,99.
Bijbehorende sessie: `4fa81608-ce22-4dd0-b44e-1408c03432e3`, broncommit
`b4a00a740b8cbf889d941f43d748d00659d4396a`.
De opnamebeelden tonen Smalltown Boy op B en Back Again op A en bevestigen de
faderoverdracht en de aanvankelijk verlaagde LOW-knop van A.
De precieze bedieningsduren hieronder komen uit de bestaande sessielog.
De beoordeling dat het kort en abrupt klonk komt van de gebruiker; dit verslag
claimt geen onafhankelijke luisterbeoordeling of gemeten audio-overlap.

| Tijd in opname, ongeveer | Lokale tijd | Vastgelegde gebeurtenis |
| --- | --- | --- |
| 08:19 | 20:09:10,290 | Aanvraag 514 start Back Again op het nog gesloten deck A |
| 08:22 | 20:09:13,458 | Start bevestigd; Smalltown Boy heeft nog 31,0 s over |
| 08:24 | 20:09:15,082 | Aanvraag 516 kiest MIX naar A met bass HOLD en vier beats; de aanvraag zag 29,9 s resterend op B |
| 08:24,6 | 20:09:15,571 | Native faderopdracht naar A, aangevraagde beweging 1,935 s bij 124 BPM |
| 08:29,9 | 20:09:20,851 | Eindstand A bevestigd; A-bass staat nog op gemeten hoek −0,339, geen dB-waarde |
| 08:32,9 | 20:09:23,890 | A-EQ neutraal bevestigd na afzonderlijke reset |

De volledige tweede MIX-bundel duurde 5,77 s inclusief waarnemen en bevestigen;
dat is niet de duur van de faderbeweging of de gemeten hoorbare overlap.
De eerste overgang, Bringing Me Joy → Smalltown Boy, had eerst een beweging
naar het midden met basoverdracht (20:06:20,313–20:06:37,124) en daarna een
afzonderlijke eindbeweging (20:06:38,213–20:06:42,593). De tweede sloeg die
tussenfase over doordat hij onmiddellijk in de verplichte afrondopties viel.

## Oorzaak

De laatste voorgestelde terugkeer in Smalltown Boy ligt op bronpositie 169,485 s
en eindigt op 200,210 s. De planner trok slechts 12 s af en bood ongeveer 19 s
bruikbare mengtijd aan. De uitvoering gaf echter al binnen de laatste 30 s
voorrang aan afronden, met uitsluitend een eindstand en twee of vier beats.
Starten en bevestigen verbruikten ook tijd. Zo kon een voorgesteld kort mengvenster
bij de eerste echte MIX-aanvraag al een verplichte snelle eindoverdracht zijn.
De bas bleef daarbij verlaagd tot de afzonderlijke EQ-reset na de overdracht.

## Lokale correctie, offline gecontroleerd

- Planner, beslisopties en uitvoeringscontrole delen dezelfde afrondgrens van
  30 s. Een gestopte opvolger krijgt daarnaast 8 s geschatte start-/uitlijnruimte.
  Dit is een conservatieve begroting, geen gemeten maximum voor bedieningstijd.
- Een kandidaat moet daarna nog minimaal vier maten mengruimte hebben. Een te
  late kandidaat schuift het bestaande late terugvalvenster niet meer op.
  De voorkeur om een track te laten ontwikkelen blijft behouden; de terugval
  wordt niet naar vóór het laatste kwart getrokken om een lange mix te halen.
- Binnen de afrondgrens biedt Jev vloeiende 8/16-beatbewegingen aan zolang beide
  klokken nog 12 s ruimte laten boven op die beweging. Anders blijven de korte
  2/4-beatopties beschikbaar. Uitlijning, trackidentiteit en EQ-herstel blijven
  vereist. De korte afrondroute houdt EQ nog steeds vast: deze patch belooft
  dus niet dat een reeds te laat begonnen overgang muzikaal volledig herstelt.
- De duurkeuze wordt vóór uitvoering opnieuw aan de actuele opties getoetst,
  zodat een inmiddels te lange beweging niet door een vertraagd antwoord glipt.

De toegevoegde en bijgewerkte regressiegevallen zijn offline geslaagd.
Een nieuwe luisterproef moet nog aantonen of de timing en klank beter zijn.

## Afzonderlijk later incident

Om 20:14:01,696 werd de derde overgang Back Again → Higher geblokkeerd met
`Het hoofdvenster van Rekordbox is niet zichtbaar.` Dat gebeurde circa vijf minuten
na de tweede overgang en verklaart diens abrupte verloop niet. De log alleen
bewijst niet waarom het venster toen niet zichtbaar was; deze correctie verandert
die blokkade niet.

## Volgende luistercontrole

De codecontroles zijn uitgevoerd. Later pas bouwen/installeren en een toegestane
luisterproef uitvoeren. Daarbij expliciet controleren: tijd van inzet, ruimte
vóór de afrondgrens, faderduur, basherstel en eventuele nieuwe blokkades.
Tot dat moment blijft de hoorbare verbetering van deze lokale wijziging onbewezen.
