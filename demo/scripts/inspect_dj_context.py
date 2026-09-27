#!/usr/bin/env python3
"""Read folder-26 metadata/caches and export the DJ question contract.

No Jev call, key access, music playback, native input or audio analysis.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev.audio_timeline import DEFAULT_CACHE, TimelineStore
from djjev.dj_context import catalogue
from djjev.state import read_library


def inspect(export=None, music_root=None, cache_dir=DEFAULT_CACHE):
    tracks = read_library(export, music_root)
    store = TimelineStore(tracks, cache_dir=cache_dir, music_root=music_root)
    genres = Counter(t.get('genre') or 'Onbekend' for t in tracks)
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'folder_26_only',
        'tracks': len(tracks),
        'with_genre': sum(bool(t.get('genre')) for t in tracks),
        'with_valid_audio_timeline': len(store.timelines),
        'genres': dict(sorted(genres.items())),
        'questions': catalogue(),
        'live_trial_performed': False,
        'model_answers_generated': False,
    }


def markdown(report):
    lines = [
        '# Jev: van DJ-vragen naar beslissingen', '',
        'Alleen muziek uit **map 26**. Jev kiest; de lokale software leest, rekent, bedient en controleert.', '',
        '## Wat nu beschikbaar is', '',
        f"- {report['tracks']} nummers uit de lokale Rekordbox-export.",
        f"- {report['with_genre']} nummers hebben een genrelabel; ontbrekende labels blijven onbekend.",
        f"- {report['with_valid_audio_timeline']} nummers hebben een geldige bestaande audioanalyse.",
        '- De nieuwe context zit in de broncode van de bestaande beslislus.',
        '- Deze controle heeft geen Jev-antwoorden opgevraagd en geen muziek gestart.', '',
        '## De vragen, in volgorde', '',
        'De situatie bepaalt welke vragen relevant zijn. Bij iedere keuze blijven continuïteit en betrouwbare bediening vooropstaan.', '',
    ]
    labels = {'active': 'Aangesloten op bestaande Jev-keuzes',
              'partial': 'Gedeeltelijk ondersteund', 'planned': 'Uitgewerkt; aparte keuze nog te bouwen',
              'observation': 'Informatie uit de tools', 'unsupported': 'Bediening nog niet beschikbaar'}
    for item in report['questions']:
        lines.extend([f"### {item['order']}. {item['question']}", '',
                      f"**Nodig:** {item['needs']}", '', f"**Antwoorden:** {item['answers']}", '',
                      f"**Stand:** {labels[item['status']]}", ''])
    lines.extend([
        '## Wat Jev daadwerkelijk terugstuurt', '',
        'De werkende lus vraagt om een actie en alleen de daarbij bruikbare parameters: nummer, faderdoel, bassdoel en bewegingsduur. '
        'Er komen geen achttien opeenvolgende netwerkaanvragen. Onafhankelijke vragen gaan samen; '
        'een faderantwoord wordt alleen uitgevoerd als Jev ook mengen koos. '
        'De overige menselijke vragen vormen een expliciete contextchecklist, geen verzonnen antwoorden.', '',
        '## Grenzen van deze eerste bouwstap', '',
        'Genrelabels zijn aanwijzingen, geen bewijs van zang, arrangement of energie. '
        'De huidige audioanalyse beschrijft bronenergie, bass en veranderingen in passages; zij beluistert niet de uiteindelijke mix. '
        'Een goede hoorbare overgang is dus nog geen automatisch gemeten resultaat.', '',
        'De bestaande voorkeur voor 32/64 maten is behouden. Er is nog geen afzonderlijke, onthouden Jev-keuze voor setrichting of totale blendlengte. '
        'Vrije cuekeuze, cuts, loops en effecten zijn nog geen toegestane bedieningsacties. '
        'De bestaande filters voor tempo en toonaard blijven gelden, ook bij verschillende genres.', '',
        'De code is lokaal aangepast en met gesimuleerde situaties getest. De geïnstalleerde app is niet vervangen en er is geen nieuwe live set uitgevoerd.', '',
        '## Logische vervolgvolgorde', '',
        '1. De bestaande offline analyse uitbreiden tot de ondersteunde nummers in map 26; ongeschikte of ontbrekende grids expliciet rapporteren.',
        '2. Een muzikale intentie en overgangsplan laten kiezen en onthouden voor de betrokken nummers, zodat volgende keuzes erop kunnen voortbouwen.',
        '3. De echte Jev-keuzes eerst op opgeslagen situaties controleren en daarna een korte set beluisteren.', '',
        '## Technische bron', '',
        '[TypeSafe Choice](https://docs.typesafe.ai/primitives/choice) en '
        '[TypeSafe function calling](https://docs.typesafe.ai/cookbooks/function_calling).', '',
    ])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', type=Path)
    parser.add_argument('--music-root', type=Path)
    parser.add_argument('--cache-dir', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    report = inspect(args.export, args.music_root, args.cache_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/'Jev-DJ-vragen.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    (args.output_dir/'Jev-DJ-vragen.md').write_text(markdown(report))
    print(json.dumps({key: report[key] for key in ('tracks','with_genre','with_valid_audio_timeline','live_trial_performed')}))


if __name__ == '__main__':
    main()
