"""The Python side must read the Swift bridge's uptime clock."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev import clock

UPTIME_NS = 157_191_494_000        # Swift DispatchTime on the macOS runner
PROCESS_NS = 56_635_291            # Apple /usr/bin/python3 time.monotonic_ns()


def darwin(monotonic, uptime, has_uptime_clock=True):
    patches = [mock.patch.object(clock.sys, 'platform', 'darwin'),
               mock.patch.object(clock.time, 'monotonic_ns', lambda: monotonic),
               mock.patch.object(clock.time, 'clock_gettime_ns', lambda clock_id: uptime, create=True)]
    if has_uptime_clock:
        patches.append(mock.patch.object(clock.time, 'CLOCK_UPTIME_RAW', 8, create=True))
    else:
        patches.append(mock.patch.object(clock.time, 'CLOCK_UPTIME_RAW', None, create=True))
    return patches


class BridgeClockTests(unittest.TestCase):
    def offset(self, patches):
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return clock._bridge_offset_ns()

    def test_process_relative_apple_python_is_shifted_onto_bridge_uptime(self):
        offset = self.offset(darwin(PROCESS_NS, UPTIME_NS))
        self.assertEqual(PROCESS_NS + offset, UPTIME_NS)

    def test_python_already_on_the_bridge_clock_is_unchanged(self):
        self.assertEqual(self.offset(darwin(UPTIME_NS + 400_000, UPTIME_NS)), 0)

    def test_missing_uptime_clock_and_other_platforms_use_monotonic_directly(self):
        self.assertEqual(self.offset(darwin(PROCESS_NS, UPTIME_NS, has_uptime_clock=False)), 0)
        with mock.patch.object(clock.sys, 'platform', 'linux'):
            self.assertEqual(clock._bridge_offset_ns(), 0)

    def test_bridge_ns_follows_patched_monotonic_clock(self):
        with mock.patch.object(clock, 'BRIDGE_OFFSET_NS', UPTIME_NS - PROCESS_NS), \
             mock.patch('time.monotonic_ns', lambda: PROCESS_NS + 2_000_000_000):
            self.assertEqual(clock.bridge_ns(), UPTIME_NS + 2_000_000_000)


class BridgeClockUseTests(unittest.TestCase):
    """A bridge frame and a bridge deadline must agree with Apple's Python."""

    def setUp(self):
        for patcher in (mock.patch.object(clock, 'BRIDGE_OFFSET_NS', UPTIME_NS - PROCESS_NS),
                        mock.patch('time.monotonic_ns', lambda: PROCESS_NS)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_fresh_bridge_frame_is_accepted_by_policy(self):
        from test_policy import library, loaded, raw
        from djjev import policy
        from djjev.state import normalize
        frame = loaded(raw())
        frame['sampledAtMonotonicNS'] = UPTIME_NS - 500_000_000
        state = normalize(frame, library(), 1)
        self.assertTrue(state['valid'])
        request = policy.prepare(state, [], False)
        self.assertIn('transport', request['questions'])

    def test_deadlines_sent_to_the_bridge_are_in_its_future(self):
        self.assertGreater(clock.bridge_ns() + 55_000_000_000, UPTIME_NS)


if __name__ == '__main__':
    unittest.main()
