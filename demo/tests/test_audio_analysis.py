"""Offline DSP fixture tests. Never import a control/environment or call Jev."""
import importlib.util
import builtins
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev.audio_analysis import (analyze_file, analyze_samples, DB_FLOOR,
                                  SAMPLE_RATE, WINDOW_FIELDS, _dependencies)
from djjev.audio_timeline import cache_document, load_cached, write_cache
from djjev.state import track_id

HAS_ESSENTIA = (importlib.util.find_spec('essentia') is not None
                and importlib.util.find_spec('numpy') is not None)


def track(start=0., beat=1):
    return {'id': track_id('synthetic.mp3'), 'file': 'synthetic.mp3', 'folder': '26',
            'bpm': 120., 'duration': 24., 'title': 'Synthetic',
            'beatgrid': [{'position_seconds': start, 'bpm': 120.,
                          'beat_in_bar': beat, 'meter': '4/4'}]}


class DependencyBoundaryTests(unittest.TestCase):
    def test_missing_optional_dependency_gives_an_offline_install_message(self):
        original = builtins.__import__
        def without_essentia(name, *args, **kwargs):
            if name == 'essentia':
                raise ImportError('optional dependency missing')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=without_essentia):
            with self.assertRaisesRegex(RuntimeError, 'requirements-audio.txt'):
                _dependencies()

    def test_analysis_rejects_wrong_sample_rate_before_dependencies(self):
        with patch('djjev.audio_analysis._dependencies', side_effect=AssertionError('DSP loaded')):
            with self.assertRaises(ValueError):
                analyze_samples([], track(), 22050)

    def test_analysis_rejects_unknown_grid_before_dependencies(self):
        with patch('djjev.audio_analysis._dependencies', side_effect=AssertionError('DSP loaded')):
            with self.assertRaises(ValueError):
                analyze_samples([], {**track(), 'beatgrid': []})

    def test_file_mutation_rejects_new_document(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / track()['file']
            path.write_bytes(b'original')
            class FakeEssentia:
                def MonoLoader(self, **kwargs):
                    def decode():
                        path.write_bytes(b'changed source with different size')
                        return []
                    return decode
            with patch('djjev.audio_analysis._dependencies', return_value=(None, None, FakeEssentia())), \
                    patch('djjev.audio_analysis.analyze_samples', return_value={
                        'duration_seconds': 8., 'extractor': {}, 'windows': []}), \
                    patch('djjev.audio_analysis.cache_document', return_value={}):
                with self.assertRaisesRegex(ValueError, 'changed during analysis'):
                    analyze_file(path, track())


@unittest.skipUnless(HAS_ESSENTIA, 'Optional offline Essentia environment is not installed')
class EssentiaTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        cls.np = np

    def sine(self, frequency, seconds, amplitude=.2):
        time = self.np.arange(round(seconds * SAMPLE_RATE), dtype=self.np.float64) / SAMPLE_RATE
        return (amplitude * self.np.sin(2 * math.pi * frequency * time)).astype(self.np.float32)

    def test_silence_is_finite_floor_not_bass_heavy_or_rhythmic(self):
        result = analyze_samples(self.np.zeros(9 * SAMPLE_RATE), track())
        self.assertEqual(len(result['windows']), 2)
        for row in result['windows']:
            self.assertEqual(set(row), WINDOW_FIELDS)
            self.assertEqual(row['energy_dbfs'], DB_FLOOR)
            self.assertEqual(row['low_energy_dbfs_estimate'], DB_FLOOR)
            self.assertEqual(row['low_fraction'], 0)
            self.assertEqual(row['onsets_per_beat'], 0)
            self.assertEqual(row['flux'], 0)
            self.assertTrue(all(math.isfinite(v) for v in row.values()))
        self.assertEqual(result['windows'][1]['bars'], .5)

    def test_equal_rms_bass_change_is_visible_without_semantic_labels(self):
        samples = self.np.concatenate((self.sine(80, 8), self.sine(1000, 8)))
        result = analyze_samples(samples, track())
        bass, treble = result['windows']
        self.assertAlmostEqual(bass['energy_dbfs'], 20 * math.log10(.2 / math.sqrt(2)), places=3)
        self.assertAlmostEqual(bass['energy_dbfs'], treble['energy_dbfs'], places=3)
        self.assertGreater(bass['low_fraction'], .97)
        self.assertLess(treble['low_fraction'], .01)
        self.assertGreater(bass['low_energy_dbfs_estimate'] - treble['low_energy_dbfs_estimate'], 20)
        self.assertEqual([row['start_bar'] for row in result['windows']], [0, 4])

    def test_gain_changes_energy_but_not_fraction_or_normalized_flux(self):
        source = self.sine(80, 8) + self.sine(1000, 8, .07)
        full = analyze_samples(source, track())['windows'][0]
        half = analyze_samples(source * .5, track())['windows'][0]
        self.assertAlmostEqual(full['energy_dbfs'] - half['energy_dbfs'], 20 * math.log10(2), places=4)
        self.assertAlmostEqual(full['low_energy_dbfs_estimate'] - half['low_energy_dbfs_estimate'],
                               20 * math.log10(2), places=4)
        self.assertAlmostEqual(full['low_fraction'], half['low_fraction'], places=5)
        self.assertAlmostEqual(full['flux'], half['flux'], places=5)

    def test_exported_downbeat_and_partial_tail_are_respected(self):
        result = analyze_samples(self.sine(80, 10), track(.25, 3))
        first, last = result['windows']
        self.assertEqual(first['start_seconds'], 1.25)
        self.assertEqual(first['end_seconds'], 9.25)
        self.assertEqual(first['bars'], 4)
        self.assertEqual(last['start_seconds'], 9.25)
        self.assertEqual(last['end_seconds'], 10)
        self.assertEqual(last['bars'], .375)

    def test_detected_attack_density_increases_for_faster_pulse_train(self):
        np = self.np
        samples = np.zeros(16 * SAMPLE_RATE, dtype=np.float32)
        rng = np.random.default_rng(21)
        pulse = (rng.standard_normal(round(.04 * SAMPLE_RATE)) *
                 np.exp(-np.arange(round(.04 * SAMPLE_RATE)) / (.005 * SAMPLE_RATE)) * .25)
        for when in list(np.arange(.25, 8, 1.)) + list(np.arange(8.25, 16, .25)):
            start = round(when * SAMPLE_RATE)
            samples[start:start + len(pulse)] += pulse
        slow, fast = analyze_samples(samples, track())['windows']
        self.assertGreater(slow['onsets_per_beat'], .2)
        self.assertGreater(fast['onsets_per_beat'], slow['onsets_per_beat'] * 2)

    def test_silent_window_after_bass_has_no_bass_power_and_roundtrips_cache(self):
        samples = self.np.concatenate((self.sine(80, 8), self.np.zeros(8 * SAMPLE_RATE)))
        metadata = track()
        result = analyze_samples(samples, metadata)
        silent = result['windows'][1]
        self.assertEqual(silent['energy_dbfs'], DB_FLOOR)
        self.assertEqual(silent['low_energy_dbfs_estimate'], DB_FLOOR)
        self.assertEqual(silent['low_fraction'], 0.)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / metadata['file']
            path.write_bytes(samples.astype(self.np.float32).tobytes())
            document = cache_document(metadata, path, result['duration_seconds'],
                                      result['extractor'], result['windows'])
            write_cache(document, Path(temp) / 'cache')
            self.assertEqual(load_cached(metadata, path, Path(temp) / 'cache'), document)

    def test_bad_samples_and_empty_coverage_are_rejected(self):
        for samples, metadata in (([], track()), ([float('nan')], track()),
                                  ([[0., 0.]], track()), ([0.] * 100, track(1))):
            with self.subTest(samples=repr(samples)[:30]):
                with self.assertRaises(ValueError):
                    analyze_samples(samples, metadata)


if __name__ == '__main__':
    unittest.main()
