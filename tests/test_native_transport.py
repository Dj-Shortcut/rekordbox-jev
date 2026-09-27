"""Compile the actual Mac control code and run simulated inputs only.

NATIVE_TRANSPORT_TESTS replaces the app entry point with the fixture harness.
No app is started, installed, captured, or connected to a control socket.
"""
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'),
                     'Requires macOS Swift/AppKit')
class NativeTransportTests(unittest.TestCase):
    def compile(self, arguments):
        result = subprocess.run(['swiftc', '-swift-version', '5', '-parse-as-library',
                                 '-target', platform.machine()+'-apple-macos14.0', *arguments],
                                capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_native_transport_cases(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='dj-jev-native-tests-') as work:
            executable = str(Path(work)/'native-tests')
            sources = ['Bridge.swift', 'MixerVision.swift', 'DJSessionHost.swift',
                       'TrackTransport.swift', 'FastObservation.swift', 'Effects.swift']
            self.compile(['-D', 'NATIVE_TRANSPORT_TESTS', '-module-cache-path',
                          str(Path(work)/'modules'),
                          *(str(root/'Sources'/name) for name in sources),
                          str(root/'tests/NativeTransportTests.swift'), '-o', executable])
            # Check role fences as well as chosen-entry deadlines, readback,
            # transport and EQ guards. These harness roles never serve sockets.
            for role in ('--demo-observer', '--demo-control'):
                with self.subTest(role=role):
                    result = subprocess.run([executable, role], capture_output=True,
                                            text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    self.assertRegex(result.stdout, r'PASS [1-9][0-9]* native transport safety cases')

    def test_widget_typecheck(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='dj-jev-widget-check-') as work:
            self.compile(['-typecheck', '-module-cache-path', str(Path(work)/'modules'),
                          *(str(root/'Sources'/name) for name in
                            ('JevWidget.swift', 'JevControls.swift', 'JevProbeControls.swift'))])


if __name__ == '__main__':
    unittest.main()
