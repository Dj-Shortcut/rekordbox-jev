"""Optional OFFLINE Essentia measurements. No model calls or physical controls.

The live DJ imports audio_timeline, never this module's DSP dependencies. These
measurements describe the source's mono downmix, before mixer EQ/gain/tempo.
They do not identify instruments, arrangement sections or perceived loudness.
"""
import math
from pathlib import Path

from .audio_timeline import cache_document, grid_definition

SAMPLE_RATE = 44100
FRAME_SIZE = 4096
HOP_SIZE = 512
DB_FLOOR = -120.0
SILENCE_POWER = 10 ** (DB_FLOOR / 10)
FLUX_SILENCE_POWER = 1e-6  # -60 dBFS: normalized spectra amplify near-silence noise.
WINDOW_FIELDS = frozenset(('start_seconds', 'end_seconds', 'start_bar', 'bars',
                          'energy_dbfs', 'low_energy_dbfs_estimate', 'low_fraction',
                          'onsets_per_beat', 'flux'))


def _dependencies():
    try:
        import numpy as np
        import essentia
        import essentia.standard as es
    except ImportError as exc:
        raise RuntimeError('Offline analysis needs demo/requirements-audio.txt '
                           'installed in a separate Python environment.') from exc
    return np, essentia, es


def _db(power):
    return max(DB_FLOOR, 10 * math.log10(max(SILENCE_POWER, power)))


def analyze_samples(samples, track, sample_rate=SAMPLE_RATE):
    """Measure non-overlapping four-bar windows from the exported first downbeat.

    Onsets are detected once over the entire source, then counted in half-open
    windows. Spectral frames are centered at their timestamp; frames crossing a
    window edge can smear its change by at most half a frame (~46 ms). Energy is
    measured directly over each window. The bass estimate is exact window power
    times its power-weighted spectral band fraction, not an isolated bass stem or
    calibrated EQ response. This keeps it bounded by the window's source energy.
    """
    if sample_rate != SAMPLE_RATE:
        raise ValueError('OnsetRate requires decoded audio at 44100 Hz.')
    grid = grid_definition(track)
    np, essentia, es = _dependencies()
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
        raise ValueError('Audio must be a nonempty finite mono sample array.')
    duration = len(audio) / SAMPLE_RATE
    if duration <= grid['start_seconds']:
        raise ValueError('Audio ends before the first complete grid anchor.')
    width = grid['window_bars'] * grid['bar_seconds']
    count = math.ceil((duration - grid['start_seconds']) / width)
    starts = [grid['start_seconds'] + i * width for i in range(count)]
    ends = [min(start + width, duration) for start in starts]
    # One stateful instance per file. Defaults are deliberately explicit where
    # they affect measurement scale; no per-file loudness normalization.
    rms = es.RMS()
    window = es.Windowing(type='hann', normalized=True, zeroPhase=True)
    spectrum = es.Spectrum(size=FRAME_SIZE)
    low = es.EnergyBandRatio(sampleRate=SAMPLE_RATE, startFrequency=30,
                             stopFrequency=250)
    flux = es.Flux(norm='L1', halfRectify=True)
    onset_times, _ = es.OnsetRate()(audio)
    onset_times = np.asarray(onset_times)
    sums = [[0., 0., 0., 0] for _ in starts]
    first_frame = True
    previous_silent = True

    def measure(center):
        nonlocal first_frame, previous_silent
        start, end = max(0, center - FRAME_SIZE // 2), min(len(audio), center + FRAME_SIZE // 2)
        frame = np.zeros(FRAME_SIZE, dtype=np.float32)
        offset = start - (center - FRAME_SIZE // 2)
        frame[offset:offset + end - start] = audio[start:end]
        power = float(rms(audio[start:end])) ** 2
        magnitudes = spectrum(window(frame))
        ratio = float(low(magnitudes)) if power > SILENCE_POWER else 0.
        total = float(magnitudes.sum())
        normalized = (magnitudes / total if power > FLUX_SILENCE_POWER and total > 0
                      else np.zeros_like(magnitudes))
        value = float(flux(normalized))
        silent = power <= FLUX_SILENCE_POWER
        # First-frame comparison is against zeros, not observed spectral motion.
        # Suppress silence-edge spikes caused solely by spectral normalization.
        if first_frame or silent or previous_silent:
            value = 0.
        first_frame, previous_silent = False, silent
        return power, ratio, value

    for center in range(0, len(audio), HOP_SIZE):
        power, ratio, value = measure(center)
        when = center / SAMPLE_RATE
        index = math.floor((when - grid['start_seconds']) / width)
        if 0 <= index < count:
            aggregate = sums[index]
            aggregate[0] += power
            aggregate[1] += power * ratio
            aggregate[2] += value
            aggregate[3] += 1

    rows = []
    for i, (start, end) in enumerate(zip(starts, ends)):
        lo, hi = math.ceil(start * SAMPLE_RATE), min(len(audio), math.ceil(end * SAMPLE_RATE))
        if hi <= lo:
            continue
        power, bass_power, flux_sum, frames = sums[i]
        if not frames:
            # Only possible for a last partial window shorter than one hop.
            p, ratio, _ = measure((lo + hi) // 2)
            power, bass_power, frames = p, p * ratio, 1
        onsets = int(np.searchsorted(onset_times, end, side='left')
                     - np.searchsorted(onset_times, start, side='left'))
        beats = (end - start) * grid['bpm'] / 60
        window_power = float(rms(audio[lo:hi])) ** 2
        low_fraction = min(1., max(0., bass_power / power)) if power > 0 and window_power > SILENCE_POWER else 0.
        rows.append({'start_seconds': start, 'end_seconds': end,
                     'start_bar': i * grid['window_bars'],
                     'bars': (end - start) / grid['bar_seconds'],
                     'energy_dbfs': _db(window_power),
                     'low_energy_dbfs_estimate': _db(window_power * low_fraction),
                     'low_fraction': low_fraction,
                     'onsets_per_beat': onsets / beats,
                     'flux': flux_sum / frames if window_power > FLUX_SILENCE_POWER else 0.})
    if not rows:
        raise ValueError('No sampled audio after the grid anchor.')
    return {'duration_seconds': duration, 'extractor': {
        'name': 'essentia', 'version': str(essentia.__version__),
        'sample_rate': SAMPLE_RATE, 'frame_size': FRAME_SIZE, 'hop_size': HOP_SIZE,
        'window': 'hann_area_normalized_times_two', 'spectral_frame_timestamp': 'center',
        'bass_band_hz': [30, 250], 'db_floor': DB_FLOOR,
        'energy': 'unwindowed_window_RMS_squared_to_dBFS',
        'low_energy': 'exact_window_RMS_squared_times_power_weighted_spectral_band_fraction_estimate',
        'low_fraction': 'power_weighted_spectral_band_fraction',
        'onsets': 'OnsetRate_whole_track_HFC_complex_1024_512',
        'flux': 'positive_L1_difference_of_L1_normalized_magnitude_spectra',
        'flux_silence_dbfs': -60,
        'basis': 'Source mono downmix before EQ/gain/tempo; stereo cancellation possible. '
                 'Onsets are detected attacks, not a drum or rhythmic-regularity classifier. '
                 'Bass energy is a spectral estimate; RMS is not perceived loudness.'},
        'windows': rows}


def analyze_file(path, track):
    """Decode a local file offline and bind measurements to its source/grid IDs."""
    path = Path(path)
    grid_definition(track)  # Reject unsupported grids before loading audio/DSP.
    if path.name != track.get('file'):
        raise ValueError('Audio filename does not match the selected track identity.')
    before = path.stat()
    _, _, es = _dependencies()
    result = analyze_samples(es.MonoLoader(filename=str(path), sampleRate=SAMPLE_RATE,
                                          downmix='mix')(), track)
    document = cache_document(track, path, result['duration_seconds'],
                              result['extractor'], result['windows'])
    after = path.stat()
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise ValueError('Audio source changed during analysis; no cache was written.')
    return document
