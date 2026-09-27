# Offline audio evidence for Jev transitions

## Summary

The active `doom_demo` now accepts optional, cached Essentia timelines for the **two loaded decks**. No new DJ action, recommendation ranking, native control or realtime DSP was added. Jev still chooses transport, fader target, complementary bass target and gesture duration.

The existing route was read before implementation: `main.session → Rekordbox/Runner → policy.prepare → Jev → resolve/applicable → environment.execute → native controls → verified state`.

## Mixing limitation found

Previously Jev saw clocks, routing, knob angles, key/BPM, red-marker alignment and verified incoming/outgoing identities. The 32-bar overlap, last-48-bars entry window and 16-bar grouping were preferences/arithmetic. Jev could not see source energy, low-frequency content or upcoming changes. Its bass instructions therefore described a generic outgoing → balanced → incoming progression.

The action vocabulary remains `hold/mix`, crossfader `hold/A/center/B`, bass `hold/A/balanced/B`, and gestures of 2/4/8/16 beats. Bass targets are knob angles, not dB. Native gestures remain bounded to 0.25–12 seconds; a compound EQ/fader action can take longer than its nominal gesture duration. A long overlap still consists of bounded movements and HOLD, not one long native operation. Incoming PLAY still starts from the track beginning; this change does not add cue selection.

## Audio features

Only four feature families are measured:

| Feature | Exact measurement | Transition relevance |
|---|---|---|
| Source energy | Essentia `RMS` over each unwindowed decoded mono interval; `10 log10(RMS²)`, floor −120 dBFS | Quieter/louder passages and energy movement; not perceptual loudness |
| Low-frequency content | `EnergyBandRatio(30–250 Hz)` on Hann magnitude spectra; power-weighted band fraction; estimated low power = exact interval RMS² × fraction | Distinguishes bass-poor and bass-rich passages, with absolute level as well as fraction |
| Attack density | Whole-track `OnsetRate`, then count its timestamps per beat in each interval | Evidence of transient activity; does not identify kicks, drums or rhythmic regularity |
| Spectral activity | `Flux(norm="L1", halfRectify=True)` on explicitly L1-normalized magnitude spectra | Changing spectral content with less sensitivity to overall gain |

Decode: mono, 44.1 kHz, without loudness normalization. Spectral frames: 4096 samples, hop 512, centered Hann. OnsetRate internally uses its own 1024/512 analysis. The first flux frame and silence-edge normalization spikes are suppressed; flux is gated below −60 dBFS. Mono downmix can cancel stereo content. Spectral frames smear edges by about 46 ms; the larger four-bar averaging is the dominant timing limitation.

Official references: [RMS](https://essentia.upf.edu/reference/std_RMS.html), [EnergyBandRatio](https://essentia.upf.edu/reference/std_EnergyBandRatio.html), [OnsetRate](https://essentia.upf.edu/reference/std_OnsetRate.html), [Flux](https://essentia.upf.edu/reference/std_Flux.html), [MonoLoader](https://essentia.upf.edu/reference/std_MonoLoader.html).

No LUFS, loudness compensation, stems, semantic section labels or large model is introduced. These are mastered **source-file** measurements, before EQ, trim, crossfader curves and tempo processing.

The synthetic equal-energy 80 Hz→1 kHz fixture keeps RMS at −16.99 dBFS while the estimated low band changes from −16.99 to −51.43 dBFS. This distinguishes a bass change that energy alone misses. The onset detector also reports some attacks on the sustained 80 Hz tone, so its density is a weak supporting measurement, never reliable evidence of percussion by itself.

## Timeline, cache and position

- Nonoverlapping **four-bar** windows begin at the first exported downbeat of a consistent constant-tempo 4/4 grid. A final partial window retains its actual coverage. Unsupported grids fall back, without inventing beat locations.
- Current window plus 8/16/32-bar lookahead summaries. Lookaheads start at the **next** four-bar window; their exact start/end and actual coverage are provided, including near track end. Before the first downbeat, current audio is unknown but the first measured windows are already visible to Jev before PLAY.
- An ordered `source_contour` provides whole-track context in nominal 16-bar source intervals, with source start/end positions and the same energy, low-band, onset and flux measurements. Long files widen the intervals in multiples of 16 bars to remain bounded to 16 segments. It is aggregated once at preload; live lookup only locates the playhead and copies this bounded contour. Earlier source positions do not prove those passages were heard, especially after seeking. No peak-passed label or action rule is derived.
- Energy summaries average linear power before converting to dB. Low fractions are energy-weighted, so a quiet bass-only section cannot dominate a loud bass-poor section's fraction.
- Descriptive window changes: ≥3 dB energy, ≥6 dB estimated low power, ≥0.25 detected attacks/beat, or +0.05 normalized flux. Energy labels require one side above −60 dBFS. These thresholds **label measurements only**; they never select an action. A location is a candidate window-boundary change, not a confirmed phrase boundary or exact bass entrance.
- Cache schema/configuration, audio SHA256, file size/mtime, track ID and exported-grid fingerprint are checked at startup. JSON checksum and numerical/coverage validation reject corrupt data. Full timelines live outside snapshots. Immutable caches are loaded once; file/grid edits require an offline rebuild and next session. Changing feature definitions requires a schema version change.
- During the loop: bounded in-memory lookup only. No decoder, filesystem access, Essentia or NumPy import. With the source contours included, measured on this Mac: approximately **0.117 ms for both decks**, mean of 10,000 lookups; representative two-deck audio context about **9.5 KB**. Two cached tracks validated/preloaded in about 0.038 s. These are local lookup measurements, not API or total control latency.

Archived normal-playback logs establish that this Rekordbox 6.8.7 setup displays **source-position seconds**, including changed tempo. Nonoverlapping 20–25-second samples: Ifthah 132→127 BPM observed slope 0.9632 vs expected 0.9621; Heaven 124.98→127 observed 1.0176 vs expected 1.0162; Noche Clara 127→127 observed 0.9987 vs expected 1.0. Hence elapsed is used directly, without multiplying pitch twice. Lookup additionally requires elapsed+remaining to agree with decoded duration within 1.5 seconds, a finite 60–200 BPM and tempo ratio 0.94–1.06. This is empirical evidence for the current setup's forward playback, not every Rekordbox version, reverse/slip/loop mode or subsecond timing guarantee. Native beat alignment remains authoritative.

## What Jev sees

Abbreviated real source context at historical request 5, Noche Clara → Ifthah:

| | Outgoing B: Noche Clara | Incoming A: Ifthah |
|---|---:|---:|
| Observed source position | 15.2 s | 1.1 s |
| Current four-bar energy | −10.75 dBFS | −22.77 dBFS |
| Estimated low-band energy | −11.26 dBFS | −24.09 dBFS |
| Low-band fraction | 0.889 | 0.738 |
| Detected attacks per beat | 2.25 | 2.00 |
| Normalized positive flux | 0.131 | 0.140 |
| Next 16 bars: energy | −8.38 dBFS | −21.83 dBFS |
| Next 16 bars: estimated low energy | −10.07 dBFS | −22.99 dBFS |

The complete context also carries both lookahead extents, change candidates, analysis IDs, clock basis, observed transition roles and approximate bars of confirmed audible overlap. Existing state continues to provide fader/EQ, remaining time, red-marker alignment and recent verified decisions.

This example exposes a roughly **12 dB source-energy difference** hidden from the original decision. Both tracks have high low-band fractions, yet their absolute low-band levels differ greatly: a fraction alone would miss that. The original answer moved toward A with bass A and a four-beat gesture. That is an observed historical answer, **not proof that Jev would choose differently with audio** and not a post-mixer loudness measurement.

## Jev behavior and safety

The prompt prioritizes the user's listening intent: let the current track, especially the opener, deliver its development and strong passage before transitioning away. A local energy dip, quiet incoming intro or prepared deck is not enough to justify starting. Measured audio helps Jev choose an opportunity after development; it does not override that intent. An RMS maximum is not a detected climax. Unknown structure falls back to the conservative late-track window, with continuity taking priority near the end.

The soft overlap preference is 32 bars for short tracks and 64 bars from five minutes onward; Jev may choose a longer flowing blend when both passages permit it, or shorten when necessary. Intermediate/unknown durations retain the 32-bar baseline. The native 2/4/8/16-beat gesture choices are unchanged: longer overlap uses gestures and HOLD, not a long queued operation. No musical option is removed or automatically selected. Track candidate selection and recommendation questions are unchanged.

Roles come from the verified muted-successor-start anchor. Before launch, only one audible deck plus a stopped closed loaded successor supports preparation roles; otherwise roles remain unknown. Missing/corrupt/unsupported data retains the old policy and physical action vocabulary.

No native or Runner control logic changed. Existing title validation, fresh observation, alignment, bounded actuation, no replay, verified readback and Stop remain intact. Audio adds a stricter applicability check: source/cache identity and source-bar token must still match; movement must agree with original playhead/time/playback/tempo within OCR tolerance. A bar change, seek, pause, tempo change or loss of referenced data discards that answer and requires a fresh choice. Measurements never authorize otherwise forbidden input.

## Running offline analysis

From `demo/`, on the tested Python 3.14 Mac:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements-audio.txt
.venv/bin/python scripts/analyze_transitions.py \
  --track-id t_1b9f318d2011a274 --track-id t_c5640d05f25c5593
```

These two IDs identify the existing folder-26 files used in the example. Elsewhere, use IDs from the exported local library. `--export`, `--music-root`, `--cache-dir` and explicit `--all` are supported; `--force` rebuilds current caches. Defaults are `~/Desktop/rekordbox.xml`, `~/Music/Music/26` and `demo/.audio-cache`. No analysis starts automatically inside the DJ. Essentia's first algorithm import took roughly 45 seconds here, entirely offline; batch selected tracks in one invocation. Reusing the two caches also succeeds under the normal Python without Essentia installed.

Only **Ifthah and Noche Clara** were analyzed in this implementation's real-audio investigation (46 and 50 windows). Other tracks gracefully use existing behavior until analyzed. Audio, caches, virtual environments and evidence are ignored by Git.

## Tests

```sh
cd demo
.venv/bin/python -m unittest discover -s tests -v
python3 -m unittest discover -s tests -q
```

The full optional environment runs actual Essentia synthetic fixtures, cache and timeline tests, compact two-deck/policy tests, replay tests and the complete existing demo control-contract suite. The normal Python run skips only optional DSP fixtures. No automated test calls the real native bridge or Jev API.

Verified after the development/contour update on 22 September 2026: **142 tests passed in 53.002 seconds, zero skips** with Essentia installed. The standard-Python fallback run passed all applicable tests in 1.123 seconds: 142 discovered, seven optional DSP tests skipped. Most of the optional run's wall time is Essentia's first import, outside the live DJ.

Fixtures cover silence, equal-RMS 80 Hz→1 kHz, gain-scaled copies, faster attack trains, first downbeat/partial tail, bass→silence boundaries, cache/file/grid mismatch, malformed JSON, energy-weighted aggregation, playhead/bar boundaries and prelaunch preview. Control coverage includes unchanged action criteria, missing analysis fallback, bar/seek answer rejection, and existing physical safety/Stop/no-replay tests.

## Evidence and replay

The existing event stream already records requests, typed answers, dispatch and verification under `request_id`. It now retains the compact context Jev actually saw in each request; raw observation logs do not duplicate those audio windows.

```sh
python3 scripts/summarize_run.py evidence/<run-id> --transitions --json
python3 scripts/summarize_run.py evidence/<run-id> --transitions --request-id 5 --json
```

The added view joins both deck contexts, transition history, actual questions/probabilities, chosen fader/bass/duration, dispatched actions and verified subsequent state. HOLD and ignored/rejected answers are retained. Legacy logs say `not_available_at_decision`. Choice responses contain no rationale: the report shows evidence and decisions, never invents why Jev chose them. The existing read-only `djjev/decision_audit.py` can evaluate enriched requests without constructing an actuator.

Local initial investigation artifacts in `evidence/audio-investigation/`: `real-transition-replay.json`, `context-example.json`, `source-clock-check.json`, `lookup-benchmark.json`, `synthetic-evidence.json`, and `transition-timelines.svg`. Historical requests 5–8 are annotated separately as `offline_annotation_not_seen_by_jev`; their original inputs/answers remain intact. That initial investigation produced no new API answers or live mix.

The subsequent authorized live run `776e5dca-44b8-4655-89c0-56db65557254` supplied both audio contexts in all 16 requests (median API time 0.441 s). Jev chose successor PLAY at outgoing position 14.5 s, then the native paired bass gesture reached the incoming neutral boundary and blocked after partial execution. This was not a completed transition. Its preparation and manual stop/EQ cleanup are separate from Jev's actions. The early choice motivated the stronger development priority and duration-dependent preferences above; the bass-actuator failure remains a separate unresolved issue.

Read-only Jev replay `91453642-8ec8-4396-b00d-2edbb6a8398c` then passed eight scoped decision checks. The recorded 14.5-second situation returned HOLD in all three repeats; an altered 160-second position returned HOLD, later/urgent positions returned PLAY B, the early no-audio case returned HOLD, and a silent set returned PLAY A. Altered positions are counterfactuals, not newly observed playback. The host used the existing credential in decision-audit mode, with no actuator constructed, and its normal configuration was restored. The exact cases and references are in local `evidence/pacing-investigation/`. These results support the changed decision behavior in those cases, not universal peak recognition or live mix quality.

The final targeted replay `4e208d9c-3bf5-4f59-a0d6-b3c5109b77b4` passed 6/7 checks: three early HOLD repeats, continued blending for the longer track, urgent completion and silent-set start. The shorter-track case did not move to the incoming endpoint in that single answer, so consistent completion at the soft duration target remains unproven. Two earlier hypothetical after-EQ cases had contradictory continuity fields; those were corrected by rebuilding the whole request and are not treated as model-failure evidence. A stricter completion-prompt experiment was not retained because it also shortened the long blend. The local pacing report preserves these intermediate results and limitations. Standard-Python regression tests were rerun after the final wording: 142 discovered, seven optional DSP skips, all applicable tests passed.

The real-track contrast and equal-energy synthetic bass change show useful information unavailable to the old policy. **Better sounding transitions and improved Jev choices are not yet demonstrated.**

## Next experiment

Test **one-bar low-frequency change localization inside the existing four-bar windows**. Four bars are about 7.6 seconds at 127 BPM—too coarse to tell Jev exactly when an incoming bass entrance occurs. Compare one-bar estimates with the current four-bar estimates at the measured changes, using source-audio inspection and paired read-only Jev decision replays. Retain the four-bar summaries for context and add finer timing only if it improves entrance localization and the resulting choices. No new physical action vocabulary is needed.
