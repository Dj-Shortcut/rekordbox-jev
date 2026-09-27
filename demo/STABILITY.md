# Stabiliteit vóór de volgende proef

De actieve route is **DJ Jev.app → Start set → ondertekende Bridge → scripts/run_session.py → demo/djjev**. De `.command`-snelkoppeling opent dezelfde app; ze heeft geen tweede sessielogica meer. Start verzorgt de bestaande voorbereiding van map 26. Er is geen Codex-proces nodig voor waarnemen, beslissen, bedienen of herstellen.

## Grenzen van herstel

- Observer/control delen ieder een native proceslock. Een verdwenen proces mag opnieuw gestart worden; een onbereikbaar proces met een nog bezette lock krijgt **geen tweede uitvoerder**. Maximaal drie herstarts per sessie.
- Fysieke opdrachten worden eenmalig verzonden. Een verbinding die vóór verzending faalt en een verloren antwoord ná mogelijke invoer hebben verschillende foutcodes. Een verloren antwoord wordt nooit als bewijs van nul invoer behandeld.
- Een hersteld proces, focusverlies of een onderbroken observatiereeks vereist twee nieuwe stabiele waarnemingen en een nieuwe Jev-keuze. Trackidentiteiten, afspelen, faders, EQ, sync/master en waar nodig uitlijning worden gecontroleerd. Herstel vereist map 26. Een onverwachte trackwissel blokkeert.
- Mixfouten blijven onder de strenge, maximaal tweevoudige reconciliatie van PR #1. Een onbekende/gedeeltelijke mix kan niet via generiek herstel herhaald worden.
- Focusherstel gebeurt alleen tijdens een actieve set: maximaal twee pogingen per sessie, minimaal vijf seconden uit elkaar. Het bevestigde scherm is leidend, niet het antwoord op een activatieverzoek.
- Een onleesbaar scherm wordt opnieuw gelezen; na twaalf seconden aanhoudende uitval blokkeert de bediening. Een lopende native observatie heeft daarnaast een eigen timeout van acht seconden.
- API-fouten worden apart gelogd. Er zijn maximaal drie nieuwe herstelvragen per sessie, met korte backoff en een nieuwe waarneming. De bestaande 429/529-backoff blijft binnen de requestdeadline. Ongeldige en te late antwoorden mogen geen actie uitvoeren. Een 401/403 blokkeert direct.
- De native host bewaakt de Python-heartbeat. Na dertig seconden zonder heartbeat blokkeert hij invoer voor die combinatie van PID en sessie-UUID en onderbreekt de runner. Een later hergebruikt PID erft de oude stopmarkering niet. Een crash wordt als onderbroken sessie vastgelegd, niet automatisch als nieuwe set gestart. Een blinde herstart zou mogelijk oude bediening en opnieuw voorbereiden introduceren.
- Een door deze runner gestart hulpproces dat de eerste statuscontrole niet haalt, wordt beëindigd en afgewacht; alleen dit nog niet voor bediening gebruikte proces mag zo worden opgeruimd. Een bestaand, onbereikbaar proces wordt nooit op basis van alleen een verdwenen socket vervangen.
- Stop en terminale fouten blokkeren verdere native invoer. Ze pauzeren de muziek niet. Een onzekere echo krijgt 45 seconden cooldown, ook als het antwoord verloren gaat. Effecten blijven standaard uitgeschakeld.

## Bewijs per sessie

`demo/evidence/<sessie-id>/` bevat `session.json`, `events.jsonl`, `bridge-status.jsonl`, `native-commands.jsonl`, `health.jsonl` en `result.json`. De host voegt `host-result.json` en bij een vastgelopen runner `host-fault.json` toe.

`result.json` begint als **incomplete**. Alleen een afgehandelde sessie krijgt `completed: true`; dat is procesafhandeling, geen oordeel over mixkwaliteit. De checkpoint bewaart de laatste geslaagde observatie, foutklasse, aanvraag, fysieke opdracht en bekende processtatussen. Ontbrekend bewijs van verstuurde of gedeeltelijke invoer blijft `null`; `possibly_partial` benoemt onzekerheid. Native opdrachtintentie wordt naar schijf geschreven vóór invoer. Bij onschrijfbare logs wordt verdere bediening gestopt. De bestaande API-sleutel wordt niet naar logs, argumenten of omgevingsvariabelen gekopieerd.

## Eén build en startflow

Bouw met `bash scripts/build-all.sh`. Dit gebruikt de bestaande vaste lokale ondertekening en maakt `build-info.json`; er wordt geen nieuwe signing-identiteit aangemaakt. Beide apps dragen dezelfde broncommit, bronhash en Bridge-protocolversie. De widget toont versie, commit en protocol. De host en Python-kern weigeren een verkeerde modus of gemengde/stale build.

De analyseomgeving, muziekanalysecache en privébibliotheek blijven installatiegegevens. De bronrepo bevat geen muziek, API-sleutels of persoonlijke bibliotheekexport. Bestaande installatiegegevens moeten bij een update behouden blijven.

Historische modules `jev_reactive.py`, `dj_session.py`, `dj_set.py`, `contextual_session.py` en `playground_live.py` zijn geen officiële set-entrypoints. Ze blijven aanwezig voor hun bestaande regressietests/onderzoek. De compatibiliteitsroute voor `jev_reactive.py --dj-test` gaat bij `doom_demo` naar dezelfde `run_session.py`; er is geen terugval naar oude DJ-logica wanneer de nieuwe kern ontbreekt.

## Proef en bewijsgrens

Offline tests omvatten socketuitval, bezette proceslocks, herstartlimieten, focuslimieten, een verloren laadantwoord, nieuwe beslissingen na herstel, API-uitputting, logs en buildmismatches. Een expliciete provider/native-fixture doorloopt twee overdrachten na observeruitval en een verloren laadantwoord. Dat bewijst de programmastroom; de fixture is geen echte Jev-aanvraag, Rekordbox-bediening of audio-oordeel.

Voor de volgende proef: open de bijgewerkte DJ Jev-app en klik Start set. Laat meerdere overgangen doorlopen zonder herstelhulp. Bij een onderbreking blijft de reden zichtbaar; de sessiemap maakt onderzoek mogelijk. Een langere proef met de werkelijk geïnstalleerde apps blijft nodig om langdurige zelfstandigheid en hoorbare kwaliteit te beoordelen. Deze implementatieronde start zelf geen live set.
