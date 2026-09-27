"""Evidence and ordered DJ questions, attached to the real policy request.

This module never selects an action, simulates a Jev answer, or runs controls.
The catalogue distinguishes implemented choices from missing capabilities.
"""
# order, id, human question, required evidence, answer vocabulary, runtime binding, status
_ROWS = (
    (1, 'direction', 'Welke sfeer wil ik neerzetten?',
     'Setopdracht, gespeelde nummers, energieverloop en eventueel publieksreacties.',
     'Opbouwen / vasthouden / ademruimte / afbouwen.', None, 'planned'),
    (2, 'opening', 'Speelt er muziek, en waarmee begin ik anders?',
     'Bevestigde afspeelstatus, hoorbare routes en kandidaten uit map 26.',
     'Nummer-ID; laden / starten / wachten.', 'transport,next_track', 'active'),
    (3, 'passage', 'Wat gebeurt er nu en straks in het huidige nummer?',
     'Afspeelpositie en beschikbare offline tijdlijn; echte sectielabels ontbreken.',
     'Gemeten energie/bass/aanvallen/verandering; onbekend waar niet gemeten.', 'audio_context', 'observation'),
    (4, 'next_intent', 'Wat moet het volgende nummer toevoegen?',
     'Setrichting, geschiedenis en vergelijkbare eigenschappen van kandidaten.',
     'Meer energie / voortbouwen / contrast / rust / onvoldoende informatie.', None, 'planned'),
    (5, 'track', 'Welk nummer kies ik?',
     'Beschikbare kandidaten: genre, tempo, toonaard, duur en recente nummers.',
     'Een beschikbaar nummer-ID uit map 26; alleen gebruikt bij laden.', 'next_track', 'active'),
    (6, 'compatibility', 'Kunnen deze twee nummers samengaan?',
     'Tempo/toonaard, routes en tijdlijnen van beide decks; zang is onbekend.',
     'Technisch toegestaan / niet toegestaan; muzikale afweging in de actievragen.', 'transport', 'partial'),
    (7, 'entry_point', 'Waar in het nieuwe nummer begin ik?',
     'Betrouwbare cuepunten, structuur en geteste seek-bediening.',
     'Nu uitsluitend begin van de track met bestaande maatuitlijning.', None, 'unsupported'),
    (8, 'technique', 'Welke overgang past bij deze combinatie?',
     'Beide passages en daadwerkelijk ondersteunde bedieningsmogelijkheden.',
     'Nu mengen met fader, bass, midden en hoog, of wachten; kort echo-accent; cut/loop niet beschikbaar.', 'transport,crossfader,bass,mid,high', 'partial'),
    (9, 'launch_window', 'Wanneer past het om de overgang te beginnen?',
     'Laatste aanhoudende terugkeer van lage tonen en aanvallen, hele tijdlijn, beide klokken; kicks en climax blijven vermoedens.',
     'Kickpatroon / laatste sectie / hoogtepunt voorbij / passage geschikt; onzeker is een geldig antwoord. Mixtijd wordt berekend.', 'kick_pattern,last_section,post_peak,entry_fit,transport', 'active'),
    (10, 'overlap', 'Hoe lang laat ik de nummers overlappen?',
     'Gemeten overlap, beschikbare tijd en beide muzikale passages.',
     'Overlap aanhouden / voortzetten / afronden; 32/64 maten zijn bestaande voorkeuren.', 'transport,crossfader', 'partial'),
    (11, 'readiness', 'Staat het nieuwe nummer werkelijk klaar?',
     'Trackidentiteit, route, sync, tempo, EQ en startpositie.',
     'Voorbereiden / EQ herstellen / klaar; bepaald uit fysieke controles.', 'transport', 'active'),
    (12, 'launch', 'Start ik het nieuwe nummer nu?',
     'Startvenster, gereedheid en genoeg tijd om uitlijning en overdracht te voltooien.',
     'play_A / play_B / hold, voor zover nu toegestaan.', 'transport', 'active'),
    (13, 'introduction', 'Hoe maak ik het nieuwe nummer hoorbaar?',
     'Beide decks spelen, bevestigde uitlijning, faderstand en bassbalans.',
     'Fader hold / A / center / B; alleen gebruikt bij mix.', 'crossfader', 'active'),
    (14, 'result', 'Werkt de combinatie zoals bedoeld?',
     'Teruggelezen bediening en bronmetingen; live mixaudio ontbreekt.',
     'Bediening bevestigd / niet bevestigd; hoorbare kwaliteit nog niet beoordeelbaar.', 'verification', 'partial'),
    (15, 'adjustment', 'Wat pas ik nu aan?',
     'Bevestigde mixerstand, beide tijdlijnen en resterende overgangstijd.',
     'hold / mix; fader- en EQ-doelen; beweging van 2 / 4 / 8 / 16 beats.', 'transport,crossfader,bass,mid,high,duration', 'active'),
    (16, 'bass_exchange', 'Wanneer neemt het nieuwe nummer de bass over?',
     'Bron-bass van beide passages, huidige EQ, overgangsrichting en resterende tijd.',
     'Bass behouden, licht/middel/diep overdragen of verdelen; geen boost boven neutraal.', 'bass', 'active'),
    (17, 'exit', 'Wanneer verdwijnt het oude nummer?',
     'Bevestigde inkomende/uitgaande rol, overlapduur en naderend einde.',
     'Fader naar inkomend deck / verder mengen / wachten; daarna oud deck stoppen.', 'transport,crossfader', 'active'),
    (18, 'cleanup', 'Is de overgang klaar en kan ik verder?',
     'Fader-eindpunt, afspeelstatus en neutrale EQ van beide decks.',
     'Oud deck stoppen / EQ herstellen / verse opvolger laden.', 'transport', 'active'),
)


def catalogue():
    keys = ('order', 'id', 'question', 'needs', 'answers', 'binding', 'status')
    return [dict(zip(keys, row)) for row in _ROWS]


def phase(state):
    """Describe the observed situation, never select a DJ action."""
    if state['busy']:
        return 'executing'
    transition = state.get('transition')
    if transition and transition['handoff_endpoint_reached']:
        return 'handoff_cleanup'
    audible = state['continuity']['audible_decks']
    if not audible:
        return 'opening_or_recovery'
    playing = [n for n, d in state['decks'].items()
               if d['playing'] and not state['routes'][n]['ended']]
    if len(playing) == 2:
        return 'overlap' if state['mixer']['aligned'] is True else 'alignment_needed'
    if state['continuity']['prepared_silent_decks']:
        return 'prepared_successor'
    return 'select_or_prepare'


_FOCUS = {
    'executing': [],
    'opening_or_recovery': [2, 5, 11, 12],
    'select_or_prepare': [3, 5, 6, 11, 18],
    'prepared_successor': [3, 6, 9, 10, 11, 12],
    'alignment_needed': [11, 14, 15],
    'overlap': [3, 10, 13, 14, 15, 16, 17],
    'handoff_cleanup': [14, 17, 18],
}

GUIDANCE = (
    'Use dj_context as an evidence checklist for the current situation. '
    'The numbered questions describe considerations, not a forced phase script. '
    'Only the actual question criteria authorize a choice. Never treat another question in this '
    'same request as already answered; MIX and LOAD branches remain conditional. '
    'Genre is an exported tag, not proof of arrangement, vocals, energy or a transition method. '
    'Missing genre does not disqualify an otherwise eligible track. Do not infer genre from a title '
    'or assume all folder-26 music is EDM. Use the actual passages where measured. '
    'Crowd response, vocal overlap and live output sound are unknown. A verified control move '
    'does not prove that the mix sounds good. Choose only supported actions; no cut, '
    'loop or arbitrary cue jump is available. Timing preferences remain soft; continuity wins.'
)


def attach(request, snapshot):
    state = request['state']
    current_phase = phase(state)
    library = {t['id']: t for t in snapshot['library'] if t.get('folder') == '26'}
    decks = {}
    for name, deck in state['decks'].items():
        track = library.get(deck['track_id'], {})
        audio = state.get('audio_context', {}).get('decks', {}).get(name, {})
        decks[name] = {
            'track_id': deck['track_id'],
            'genre': track.get('genre') or None,
            'genre_source': 'rekordbox_export' if track.get('genre') else 'unknown',
            'audio_evidence': audio.get('status', 'missing_analysis'),
            'vocal_activity': None,
            'semantic_section': None,
        }
    active = _FOCUS[current_phase]
    state['dj_context'] = {
        'scope': 'folder_26_only',
        'situation': current_phase,
        'decks': decks,
        'checklist': [{'order': item['order'], 'question': item['question'],
                       'answer_used_by': item['binding']}
                      for item in catalogue() if item['order'] in active],
        'available_answers': {name: list(q['criteria'])
                              for name, q in request['questions'].items() if name != 'next_track'},
        'unmeasured': ['live_mix_audio', 'vocal_activity', 'semantic_sections', 'crowd_response'],
        'not_implemented': ['independent_set_direction', 'cue_selection', 'cut', 'loop', 'effects_beyond_subtle_echo', 'recording_control'],
        'priority': 'Continuity and physical validity apply in every situation. Unknown evidence stays unknown.',
    }
    state['goal'] += ' ' + GUIDANCE
    for name, question in request['questions'].items():
        # IDs are not visible to the model, so the instructions name the state.
        question['instructions'] += ' Read dj_context for current evidence, missing information and supported capabilities.'
        if name == 'next_track':
            question['instructions'] += (
                ' Use candidate genre tags only as metadata hints alongside the set history and current track. '
                'Do not equate a genre label with bass, vocals or guaranteed compatibility.')
    return request
