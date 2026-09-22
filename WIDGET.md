# DJ Jev

Open **DJ Jev.app**. Dit venster zweeft boven Rekordbox en toont de laatste vraag- en antwoordkaarten, de geselecteerde keuzes en kansbalkjes. Het kleine tijdstip bovenaan geeft aan van welke aanvraag de antwoorden zijn. Onderaan staat **Start proef**. Vergroten of verkleinen kan met de vensterrand.

**De huidige proef leest Rekordbox en laat Jev een track kiezen; hij bedient Rekordbox niet.** De knop wordt tijdens de proef uitgeschakeld. Een nieuwe vraag en het echte antwoord verschijnen automatisch. Als geen vraag mogelijk is, verschijnt een nieuwe melding in plaats van alleen een oud antwoord. `Proef klaar` betekent dat de aanvraag is afgelopen, niet dat een mix geslaagd is.

De knop gebruikt de al draaiende Rekordbox Bridge. Alleen die Bridge gebruikt de bestaande opgeslagen sleutel. De widget leest geen sleutel en opent geen wachtwoordinvoer. Vraag- en antwoordgebeurtenissen worden buiten Documenten gelezen. Er wordt via deze knop nooit teruggevallen op de oude mixtest: de native hostroute weigert een andere configuratie dan `contextual_observer`. Oude bedieningsproeven vereisen nu de expliciete CLI-optie `--legacy-control` en zijn niet bereikbaar via deze knop.

De startknop is live aangeklikt op 22 september: Jev antwoordde **Attract**, het antwoord verscheen in de widget en de proef verstuurde nul bedieningscommando's. De bestaande Bridge bleef hetzelfde proces; alleen de widget is opnieuw gebouwd met de bestaande ondertekening.

Zie [de actuele stand](UPDATE-21-SEPTEMBER.md) voor wat actief is en de blokkering bij het vervangen van de native Bridge.

Bouwen: `bash scripts/build-widget.sh`. Diagnostiek zonder sleuteltoegang: `DJ Jev.app/Contents/MacOS/jev-widget --inspect`.
