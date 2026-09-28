"""Fixture tests for green POSITION_MARK parsing. No file, network or live library I/O."""
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev.rekordbox_cues import (collection_tracks, green_entry_cues, is_green,
                                  load_rekordbox_xml)

GREEN = {'Red': '40', 'Green': '226', 'Blue': '20'}
RED = {'Red': '226', 'Green': '40', 'Blue': '20'}


def track_xml(marks, total_time=None):
    body = ''.join(
        '<POSITION_MARK Name="{Name}" Type="{Type}" Start="{Start}" Num="{Num}"{color}/>'.format(
            Name=mark['Name'], Type=mark['Type'], Start=mark['Start'], Num=mark['Num'],
            color=''.join(f' {k}="{v}"' for k, v in mark.get('color', {}).items()))
        for mark in marks)
    total_time_attr = f' TotalTime="{total_time}"' if total_time is not None else ''
    return ET.fromstring(f'<TRACK TrackID="1" Name="Synthetic"{total_time_attr}>{body}</TRACK>')


class IsGreenTests(unittest.TestCase):
    def test_dominant_green_channel_is_green(self):
        self.assertTrue(is_green(40, 226, 20))

    def test_dominant_red_channel_is_not_green(self):
        self.assertFalse(is_green(226, 40, 20))

    def test_gray_is_not_green(self):
        self.assertFalse(is_green(128, 128, 128))

    def test_dim_green_below_minimum_level_is_not_green(self):
        self.assertFalse(is_green(0, 60, 0))

    def test_rejects_out_of_range_channel(self):
        with self.assertRaises(ValueError):
            is_green(0, 300, 0)

    def test_rejects_non_integer_channel(self):
        with self.assertRaises(ValueError):
            is_green(0, 128.5, 0)


class GreenEntryCuesTests(unittest.TestCase):
    def test_requires_a_track_element(self):
        with self.assertRaises(ValueError):
            green_entry_cues(ET.fromstring('<NOT_A_TRACK/>'))

    def test_requires_track_tag_not_none(self):
        with self.assertRaises(ValueError):
            green_entry_cues(None)

    def test_single_green_cue_is_selected(self):
        track = track_xml([
            {'Name': 'Drop', 'Type': '0', 'Start': '32.5', 'Num': '-1', 'color': GREEN},
        ])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'single')
        self.assertEqual(result['selected']['start_seconds'], 32.5)
        self.assertEqual(result['selected']['name'], 'Drop')
        self.assertEqual(result['selected']['color'], {'red': 40, 'green': 226, 'blue': 20})
        self.assertEqual(result['selected']['cue_kind'], 'memory_cue')
        self.assertEqual(result['ignored'], [])
        self.assertIsNone(result['duration_seconds'])
        self.assertEqual(result['duration_status'], 'missing')

    def test_green_hot_cue_slot_is_labeled_hot_cue_not_memory_cue(self):
        track = track_xml([
            {'Name': 'A', 'Type': '0', 'Start': '1', 'Num': '0', 'color': GREEN},
        ])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'single')
        self.assertEqual(result['selected']['cue_kind'], 'hot_cue')
        self.assertEqual(result['selected']['num'], 0)

    def test_cue_within_known_track_duration_is_kept(self):
        track = track_xml([
            {'Name': 'Drop', 'Type': '0', 'Start': '32.5', 'Num': '-1', 'color': GREEN},
        ], total_time='240')
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'single')
        self.assertEqual(result['duration_seconds'], 240.0)
        self.assertEqual(result['duration_status'], 'known')

    def test_cue_beyond_known_track_duration_is_rejected(self):
        track = track_xml([
            {'Name': 'Drop', 'Type': '0', 'Start': '300', 'Num': '-1', 'color': GREEN},
        ], total_time='240')
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none')
        self.assertEqual(result['duration_status'], 'known')
        self.assertEqual(result['ignored'][0]['reason'], 'start_beyond_track_duration')
        self.assertEqual(result['ignored'][0]['duration_seconds'], 240.0)

    def test_cue_within_final_fractional_second_of_whole_second_duration_is_kept(self):
        track = track_xml([
            {'Name': 'End', 'Type': '0', 'Start': '240.4', 'Num': '-1', 'color': GREEN},
        ], total_time='240')
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'single')
        self.assertEqual(result['selected']['start_seconds'], 240.4)

    def test_cue_beyond_missing_duration_is_not_rejected_but_duration_status_is_explicit(self):
        track = track_xml([
            {'Name': 'Drop', 'Type': '0', 'Start': '9999', 'Num': '-1', 'color': GREEN},
        ])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'single')
        self.assertEqual(result['duration_seconds'], None)
        self.assertEqual(result['duration_status'], 'missing')

    def test_invalid_duration_attribute_is_explicit_and_does_not_reject_cues(self):
        track = track_xml([
            {'Name': 'Drop', 'Type': '0', 'Start': '9999', 'Num': '-1', 'color': GREEN},
        ], total_time='not-a-number')
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'single')
        self.assertEqual(result['duration_seconds'], None)
        self.assertEqual(result['duration_status'], 'invalid')

    def test_negative_duration_attribute_is_invalid(self):
        track = track_xml([
            {'Name': 'Drop', 'Type': '0', 'Start': '1', 'Num': '-1', 'color': GREEN},
        ], total_time='-5')
        result = green_entry_cues(track)
        self.assertEqual(result['duration_status'], 'invalid')

    def test_non_finite_duration_attribute_is_invalid(self):
        track = track_xml([
            {'Name': 'Drop', 'Type': '0', 'Start': '1', 'Num': '-1', 'color': GREEN},
        ], total_time='inf')
        result = green_entry_cues(track)
        self.assertEqual(result['duration_status'], 'invalid')

    def test_no_colored_marks_gives_none_status(self):
        track = track_xml([{'Name': '', 'Type': '0', 'Start': '10', 'Num': '-1', 'color': {}}])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none')
        self.assertIsNone(result['selected'])
        self.assertEqual(result['ignored'][0]['reason'], 'uncolored_or_invalid_color')

    def test_red_colored_mark_is_ignored_not_green(self):
        track = track_xml([{'Name': '', 'Type': '0', 'Start': '10', 'Num': '-1', 'color': RED}])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none')
        self.assertEqual(result['ignored'][0]['reason'], 'not_green')

    def test_green_loop_marker_is_not_treated_as_a_cue(self):
        track = track_xml([
            {'Name': 'Loop', 'Type': '4', 'Start': '5', 'Num': '-1', 'color': GREEN},
        ])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none')
        self.assertEqual(result['ignored'][0]['reason'], 'not_a_cue_type')

    def test_green_hotcue_a_is_selected_by_color_not_by_slot(self):
        track = track_xml([
            {'Name': 'A', 'Type': '0', 'Start': '1', 'Num': '0', 'color': RED},
        ])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none', 'hotcue A with a non-green color must not be treated as green')

    def test_green_name_without_green_color_is_not_green(self):
        track = track_xml([
            {'Name': 'green intro', 'Type': '0', 'Start': '1', 'Num': '-1', 'color': RED},
        ])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none')

    def test_multiple_green_cues_are_explicit_and_not_silently_resolved(self):
        track = track_xml([
            {'Name': 'First', 'Type': '0', 'Start': '10', 'Num': '-1', 'color': GREEN},
            {'Name': 'Second', 'Type': '0', 'Start': '90', 'Num': '-1', 'color': GREEN},
        ])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'ambiguous')
        self.assertIsNone(result['selected'])
        self.assertEqual([cue['name'] for cue in result['cues']], ['First', 'Second'])

    def test_cues_are_ordered_by_source_time_not_document_order(self):
        track = track_xml([
            {'Name': 'Later', 'Type': '0', 'Start': '90', 'Num': '-1', 'color': GREEN},
            {'Name': 'Earlier', 'Type': '0', 'Start': '10', 'Num': '-1', 'color': GREEN},
        ])
        result = green_entry_cues(track)
        self.assertEqual([cue['name'] for cue in result['cues']], ['Earlier', 'Later'])

    def test_invalid_start_is_ignored_with_a_reason(self):
        track = track_xml([{'Name': '', 'Type': '0', 'Start': 'not-a-number', 'Num': '-1', 'color': GREEN}])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none')
        self.assertEqual(result['ignored'][0]['reason'], 'invalid_or_missing_start')

    def test_negative_start_is_ignored(self):
        track = track_xml([{'Name': '', 'Type': '0', 'Start': '-1', 'Num': '-1', 'color': GREEN}])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none')

    def test_non_finite_start_is_ignored(self):
        track = track_xml([{'Name': '', 'Type': '0', 'Start': 'inf', 'Num': '-1', 'color': GREEN}])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none')

    def test_start_beyond_bound_is_ignored(self):
        track = track_xml([{'Name': '', 'Type': '0', 'Start': str(25 * 3600), 'Num': '-1', 'color': GREEN}])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none')

    def test_out_of_range_color_channel_is_treated_as_invalid_color(self):
        track = track_xml([{'Name': '', 'Type': '0', 'Start': '1', 'Num': '-1',
                            'color': {'Red': '0', 'Green': '999', 'Blue': '0'}}])
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'none')
        self.assertEqual(result['ignored'][0]['reason'], 'uncolored_or_invalid_color')

    def test_missing_num_is_none_not_a_crash(self):
        marks = ''.join([
            '<POSITION_MARK Name="Drop" Type="0" Start="1" Red="40" Green="226" Blue="20"/>'])
        track = ET.fromstring(f'<TRACK TrackID="1">{marks}</TRACK>')
        result = green_entry_cues(track)
        self.assertEqual(result['status'], 'single')
        self.assertIsNone(result['selected']['num'])
        self.assertEqual(result['selected']['cue_kind'], 'unknown')

    def test_too_many_position_marks_is_rejected(self):
        marks = [{'Name': '', 'Type': '0', 'Start': str(i), 'Num': '-1', 'color': {}} for i in range(300)]
        with self.assertRaises(ValueError):
            green_entry_cues(track_xml(marks))


class LoadRekordboxXmlTests(unittest.TestCase):
    def test_parses_well_formed_document(self):
        root = load_rekordbox_xml('<DJ_PLAYLISTS Version="1.0.0"><COLLECTION Entries="0"/></DJ_PLAYLISTS>')
        self.assertEqual(root.tag, 'DJ_PLAYLISTS')

    def test_accepts_bytes_input(self):
        root = load_rekordbox_xml(b'<DJ_PLAYLISTS Version="1.0.0"><COLLECTION Entries="0"/></DJ_PLAYLISTS>')
        self.assertEqual(root.tag, 'DJ_PLAYLISTS')

    def test_rejects_doctype_declarations(self):
        with self.assertRaises(ValueError):
            load_rekordbox_xml('<!DOCTYPE foo [<!ENTITY xxe "boom">]><DJ_PLAYLISTS/>')

    def test_rejects_malformed_xml(self):
        with self.assertRaises(ValueError):
            load_rekordbox_xml('<DJ_PLAYLISTS>')

    def test_collection_tracks_returns_track_elements(self):
        root = load_rekordbox_xml(
            '<DJ_PLAYLISTS><COLLECTION Entries="1">'
            '<TRACK TrackID="1" Name="Synthetic"/></COLLECTION>'
            '<PLAYLISTS><NODE Type="0" Name="ROOT"><NODE Name="P"><TRACK Key="1"/></NODE></NODE></PLAYLISTS>'
            '</DJ_PLAYLISTS>')
        tracks = collection_tracks(root)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].get('Name'), 'Synthetic')

    def test_collection_tracks_requires_collection_element(self):
        with self.assertRaises(ValueError):
            collection_tracks(load_rekordbox_xml('<DJ_PLAYLISTS/>'))


if __name__ == '__main__':
    unittest.main()
