"""The command shortcut opens the same validated app, without another start route."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import start_standalone as launcher


class StandaloneLauncher(unittest.TestCase):
    def test_validated_shortcut_only_opens_widget(self):
        run = Mock(); validate = Mock(return_value={'commit':'fixture'})
        root = Path('/fixture')
        self.assertEqual(launcher.start(root, run=run, validate=validate), {'commit':'fixture'})
        validate.assert_called_once_with(root)
        run.assert_called_once_with(['/usr/bin/open', '/fixture/DJ Jev.app'], check=True)

    def test_invalid_build_opens_nothing(self):
        run = Mock()
        with self.assertRaisesRegex(RuntimeError, 'build'):
            launcher.start(Path('/fixture'), run=run, validate=Mock(side_effect=RuntimeError('build mismatch')))
        run.assert_not_called()

    def test_open_failure_is_reported_without_retry(self):
        run = Mock(side_effect=subprocess.CalledProcessError(1, 'open'))
        with self.assertRaises(subprocess.CalledProcessError):
            launcher.start(Path('/fixture'), run=run, validate=Mock(return_value={}))
        self.assertEqual(run.call_count, 1)
