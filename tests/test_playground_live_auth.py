import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import playground_live


class CredentialLifecycle(unittest.TestCase):
    def tearDown(self):
        playground_live._API_KEY = None

    def test_missing_pipe_key_does_not_launch_keychain_process(self):
        playground_live._API_KEY = None
        with patch('subprocess.run', side_effect=AssertionError('No Keychain subprocess allowed')):
            with self.assertRaises(RuntimeError):
                playground_live.key()

    def test_widget_key_reused_without_external_access(self):
        with patch('subprocess.run', side_effect=AssertionError('No Keychain subprocess allowed')):
            playground_live.configure_key('test-only-placeholder')
            self.assertEqual(playground_live.key(), 'test-only-placeholder')
            self.assertEqual(playground_live.key(), 'test-only-placeholder')

    def test_rejects_invalid_pipe_value(self):
        for value in ['', 'bad\nvalue', None, 'x'*4097]:
            with self.assertRaises(ValueError):
                playground_live.configure_key(value)


if __name__ == '__main__':
    unittest.main()
