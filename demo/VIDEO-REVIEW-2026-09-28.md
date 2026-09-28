# First transition: Two Faced → Didn't Miss You

Evidence: user recording `Schermopname 2026-09-28 om 14.50.59.mov` and local session
`601ae780-2006-4cc6-924f-eac2b154ab1a`, running source commit
`7501606d7cd18bd01795b508ed6b699bee425ac4`. Times below are Brussels local time.
The user reports poor audible mixing. The findings below concern saved control
events and video frames; they are not an independent listening assessment.

## What happened

* 14:53:04: Two Faced was confirmed playing at 122 BPM, about 107.6 seconds long.
* 14:53:12: Didn't Miss You was prepared on B, stopped with its bass reduced.
* 14:54:26: B was confirmed playing with only 24.8 seconds remaining on A.
* 14:54:28: the first mix gesture moved directly to B over eight beats
  (3.934 seconds). No center blend was established. B's bass remained reduced.
* 14:54:36: B's EQ reset was confirmed, separately after the fader handoff.
* 14:54:54: preparation of the following track had failed repeatedly and control
  was blocked. This is a separate unresolved problem; the first preparation
  failure reported that Rekordbox was no longer foreground. This does not
  establish who or what changed focus.

## Two different problems in entry planning

The fallback restricted the launch window to the last quarter: 26.9 seconds.
The completion guard already requires 30 seconds, before accounting for launch
or a blend. Thus the fallback itself delayed entry until the next stage could
only finish the handoff. This is an internal budgeting contradiction.

More fundamentally, the outgoing activity detector grouped almost all of Two
Faced into one continuous section (0.281–102.576 seconds). It proposed no return
and therefore no x1/x2 entry slots. The model saw four-bar measurements and
sixteen-bar averages, not recognized musical phrases. For example, its saved
lookahead at 41.6 seconds did show an energy rise at 63.232 seconds, but the
return detector did not identify a qualifying break/return. This does not prove
that either point is the correct musical entrance.

The user identifies approximately “44:4” as the ideal entry and confirmed that
this means about **44 seconds elapsed**, not a bar/beat counter. This is a
listening reference. It must not be silently encoded as a universal duration, a percentage, or proof
that the current analysis recognizes that musical event.

## Required priority

The user's musical entry and phrase preferences, the incoming track's first
drop when applicable, and both arrangements take priority over a three-quarter
duration heuristic. Short and long tracks need different treatment; alternative
arrangements must not be forced into a single break/return template. Starting a
blend and completing the outgoing track are distinct musical events.

## Local corrections and their limits

The local correction makes the fallback reserve launch/alignment, at least
four blend bars, and completion, with a conservative pitch allowance. For this
107.6-second/122-BPM case it opens around 61.7 seconds elapsed, instead of 80.7.
This prevents the fallback from inevitably entering inside the completion
margin. **It does not solve recognition of the user's ideal musical entry.**

An explicit `entry_fit=alternative` judgment now permits a supported CURRENT
passage to take priority over the final-return template and percentage fallback.
The model must also assess the two-track arrangement; unknown suitability still
uses the fallback. This path cannot claim an exact x target. It preserves native
physical guards and does not locally manufacture a PLAY decision. The regression
at 44 seconds checks that this alternative decision is accepted while unknown,
protect and unsuitable judgments still cannot launch early. The listening
reference is in the test only, not a track-specific runtime timestamp.

## Faster audible entry

The user clarified that the incoming track should become audible sooner, while
the complete overlap need not be shorter. Once a known successor is playing,
aligned, still closed and properly prepared, the first opening now offers a
2/4-beat center gesture (about 1–2 seconds at 122 BPM) and keeps EQ unchanged.
The next observation restores the usual EQ and gesture choices. MIX/HOLD remain
model choices; an urgent completion retains its separate behavior. The overall
overlap preferences are unchanged. A stopped successor already at source zero
also avoids the redundant rewind command/readback before a next-bar launch;
nonzero positions and realignment still rewind and verify.

The two-handoff integration fixture checks center before each endpoint in both
directions. It uses simulated native control and fixture decisions, not Jev
inference or live audio. Shorter configured gesture time is not a measured
end-to-end audible latency improvement. User-provided green cue markers are a
separate pending integration; these changes do not read them.

Silent tests including the faster audible-entry changes: 311 tests on both Apple Python 3.9 and Homebrew Python 3.14,
seven skips on each. The regression checks that a center blend remains available
after the launch reserve. Tests do not show that Jev selects the desired phrase
or that the resulting transition sounds good. This local correction has not
been installed and no new live mix has been started during this investigation.
