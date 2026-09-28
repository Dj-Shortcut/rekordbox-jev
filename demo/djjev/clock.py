"""Monotonic nanoseconds on the Swift bridge's clock.

The bridge stamps frames and checks deadlines with DispatchTime uptime
(mach_absolute_time, CLOCK_UPTIME_RAW). Most Pythons report the same value
from time.monotonic_ns(), but Apple's /usr/bin/python3 counts from process
start, so every frame looked stale and every deadline already expired.
The offset is measured once; time.monotonic_ns() stays the only live read,
so tests that patch it keep controlling this clock.
"""
import sys
import time

_OFFSET_TOLERANCE_NS = 1_000_000_000


def _bridge_offset_ns():
    uptime_clock = getattr(time, 'CLOCK_UPTIME_RAW', None)
    if sys.platform != 'darwin' or uptime_clock is None:
        return 0
    offset = time.clock_gettime_ns(uptime_clock) - time.monotonic_ns()
    return offset if abs(offset) > _OFFSET_TOLERANCE_NS else 0


BRIDGE_OFFSET_NS = _bridge_offset_ns()


def bridge_ns():
    """Current time comparable with the bridge's sampledAtMonotonicNS."""
    return time.monotonic_ns() + BRIDGE_OFFSET_NS
