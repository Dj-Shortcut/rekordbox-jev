"""Run the actual Swift pixel-position matcher without opening Rekordbox."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


class MarkerAlignmentTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'Requires macOS Swift/AppKit')
    def test_marker_alignment_cases(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='dj-jev-marker-tests-') as work:
            executable = str(pathlib.Path(work) / 'marker-tests')
            compilation = subprocess.run([
                'swiftc', '-module-cache-path', str(pathlib.Path(work) / 'modules'),
                str(root / 'Sources' / 'MixerVision.swift'),
                str(root / 'tests' / 'MarkerAlignmentTests.swift'), '-o', executable,
            ], capture_output=True, text=True)
            self.assertEqual(compilation.returncode, 0, compilation.stderr)
            result = subprocess.run([executable], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS 91 marker alignment cases', result.stdout)


if __name__ == '__main__':
    unittest.main()
