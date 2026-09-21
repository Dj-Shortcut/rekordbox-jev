"""Read source-file energy. Requires numpy and soundfile; does not record system audio."""
import argparse
import json
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
MUSIC = Path.home()/'Music/Music/26'


def analyse(filename):
    path = (MUSIC/filename).resolve(strict=True)
    if path.parent != MUSIC.resolve() or path.suffix.lower() != '.mp3':
        raise ValueError('Audio buiten map 26.')
    levels = []
    with sf.SoundFile(path) as audio:
        rate = audio.samplerate
        duration = len(audio)/rate
        while True:
            block = audio.read(rate, dtype='float32', always_2d=True)
            if not len(block):
                break
            mono = block.mean(axis=1)
            rms = float(np.sqrt(np.mean(mono*mono)))
            spectrum = np.abs(np.fft.rfft(mono*np.hanning(len(mono))))**2
            frequencies = np.fft.rfftfreq(len(mono), 1/rate)
            low = float(spectrum[(frequencies >= 25) & (frequencies <= 180)].sum())
            total = float(spectrum.sum())
            levels.append({'second': len(levels), 'rms_dbfs': round(20*np.log10(max(rms, 1e-9)), 2),
                           'low_energy_fraction': round(low/max(total, 1e-12), 4)})
    return {'source': 'Decoded MP3 samples; one-second measurements', 'file': filename,
            'file_size': path.stat().st_size, 'file_modified_ns': path.stat().st_mtime_ns,
            'duration_seconds': duration, 'levels': levels,
            'limits': 'Source-file energy only. Not live speaker output, vocal detection, kick phase or phrase detection.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+')
    args = parser.parse_args()
    output = ROOT/'evidence/audio-analysis.json'
    results = json.loads(output.read_text()) if output.exists() else {}
    for filename in args.files:
        results[filename] = analyse(filename)
        print(json.dumps({'file': filename, 'duration_seconds': results[filename]['duration_seconds']}))
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
