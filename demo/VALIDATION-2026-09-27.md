# Live verificatie — 27 september 2026

De geïnstalleerde DJ Jev-app maakte bij openen zelf verbinding. Daarna werd één keer op **Start set** gedrukt. Tijdens de hieronder beschreven set waren er geen handmatige laadacties, transportacties, fader- of EQ-bewegingen van Codex. Alleen de lokale logboeken werden gelezen.

Run: `8863967e-b1d5-4fa6-8e2b-6d7d67f08d9d`. Bewijsvenster: 681,1 seconden (11 minuten en 21 seconden). Muziek uitsluitend uit map 26.

| Stap | Gecontroleerd resultaat |
| --- | --- |
| Start | Attract geladen en gestart; UH HUH geladen en stil voorbereid |
| Eerste overdracht | Attract → UH HUH; fader eindstand bereikt met 22,5 seconden op de uitgaande klok |
| Eerste opruiming | Attract gestopt en EQ hersteld; Acido geladen en voorbereid |
| Tweede overdracht | UH HUH → Acido; fader eindstand bereikt met 22,3 seconden op de uitgaande klok |
| Tweede opruiming | EQ van Acido hersteld, UH HUH gestopt en EQ hersteld |
| Vervolg | I Feel Love (Extended Mix) geladen en voorbereid terwijl Acido verder speelde |

De 30-secondenmarge onderbrak in beide overgangen een afgeronde, leesbare EQ-reeks voor een nieuwe beoordeling. Na twee onafhankelijke stabiele waarnemingen koos Jev uit de expliciet beperkte afrondopties. De oude relatieve beweging werd niet herhaald. Er waren nul blokkerende uitvoerings- of herstelfouten. Vier ongeldige modelantwoorden zijn verworpen; de volgende beoordeling ging gewoon verder.

## Herstelde laadfout

De eerdere vastloper `Nieuwe bediening werd al verstuurd; verouderde waarneming niet opnieuw uitgevoerd` ontstond vóór het selecteren van een browserrij. De vervangingscontrole bouwde voor vier routepixels de volledige mixeranalyse opnieuw op. Daardoor kon een verse opname tijdens de controle zijn bedieningsdeadline verliezen.

De laad- en openingscontroles lezen nu alleen de benodigde routetoewijzing. Na browserherkenning en alle vervangingscontroles moet nog 150 ms invoermarge overblijven; anders wordt vóór de klik opnieuw waargenomen. De grens van 750 ms, titelcontrole, stilstaand/gesloten doeldeck, mapbeperking en bescherming tegen het herhalen van verzonden invoer blijven gelden.

## Technische controles en grenzen

- 209 Python-controles: 202 geslaagd, 7 optionele DSP-controles overgeslagen.
- 444 native transportcontroles geslaagd; geïnstalleerde app gebouwd en ondertekening gecontroleerd.
- Deze proef liep zonder aangesloten DDJ-controller. Audio-uitvoer en hoorbare/muzikale kwaliteit zijn niet gemeten.
- Rekordbox moet zichtbaar, in de ondersteunde indeling en vooraan blijven; de app gebruikt lokale schermherkenning en GUI-bediening.
- Twee geslaagde cycli bewijzen niet alle muziekcombinaties, externe vensterinteracties of langdurig ononderbroken gebruik.
- Opname en effecten waren niet onderdeel van de proef en zijn niet geactiveerd.

De lokale bronregistratie staat in `dj-jev-demo/evidence/8863967e-b1d5-4fa6-8e2b-6d7d67f08d9d/events.jsonl` naast deze broncodemap. Muziekbestanden, sleutels en uitvoerlogboeken worden niet in Git opgenomen.
