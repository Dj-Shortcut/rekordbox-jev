# Green cue and 3-band waveform data foundation

## Scope

This is a **data-extraction layer only**: two new stdlib-only modules,
`djjev/rekordbox_cues.py` and `djjev/three_band.py`, plus their fixture
tests. Nothing here reads a live Rekordbox library, opens the app, plays
audio, or changes any control/integration file (`state.py`,
`audio_timeline.py`, `policy.py`, `entry_timing.py`, `entry_grid.py`,
`musical_timing.py`, `transition_budget.py`, `environment.py`, `runner.py`,
Swift). No decision, entry-timing rule or "the feature is active" claim is
made by this work — that integration is intentionally left to the owner of
those files.

## Why: what "green cue" means to this project

An operator-placed **green** memory cue marks the point the DJ actually
intends the incoming track to become **audible**, not merely a load or
mute-start point, and not an arbitrary offset applied on top of it. Short
and long tracks, and how the incoming track is arranged, change where that
point should be — this module does not decide that; it only reports where
the operator put it, in source time, with its original identity intact.

## `djjev/rekordbox_cues.py`

### Contract

- `green_entry_cues(track_element)`: takes an already-parsed
  `xml.etree.ElementTree` `<TRACK>` element and returns
  `{'status', 'cues', 'selected', 'ignored', 'duration_seconds',
  'duration_status'}`.
  - Only `POSITION_MARK` children with `Type="0"` (a cue, not a loop/fade/
    load marker) and an explicit, valid `Red`/`Green`/`Blue` color
    classified green by `is_green()` are included.
  - A green cue with `Num == -1` is a **memory cue** (unassigned to a pad); a
    green cue with `Num >= 0` is a **hot cue** (an assigned lettered pad).
    Both are equally valid green entry points and are found by color alone —
    `Num`/`Name` never decide "green" — but each record's `cue_kind` field
    (`'hot_cue'` / `'memory_cue'` / `'unknown'`) makes the distinction
    explicit for the caller.
  - `status` is `'none'`, `'single'`, or `'ambiguous'`. `selected` is only
    populated for `'single'`; **an ambiguous result is never silently
    resolved to the first or earliest cue** — that choice is left to the
    caller's own entry-timing policy.
  - The `TRACK` element's own `TotalTime` attribute (seconds) is read once.
    When it parses to a finite, nonnegative, plausible value
    (`duration_status == 'known'`), any cue whose `Start` exceeds it is
    rejected (`start_beyond_track_duration`) instead of being kept as if it
    were still inside the track. When `TotalTime` is absent or unusable,
    `duration_status` is `'missing'`/`'invalid'` and **no such rejection
    happens** — the caller can see explicitly that duration was not
    validated, rather than mistaking silence for a validated bound.
  - Each cue record carries `index` (original child position),
    `start_seconds` (finite, source time, validated `0 <= t <= 24h`),
    `name`, `num` (hot-cue slot or `None` for a memory cue), `cue_kind`,
    `type`/`type_code`, and `color`. `num`/`name`/`cue_kind` are identity
    metadata only — color is the sole basis for "green".
  - `ignored` lists every skipped mark with a reason
    (`not_a_cue_type`, `uncolored_or_invalid_color`, `not_green`,
    `invalid_or_missing_start`, `start_beyond_track_duration`) for
    diagnostics.
  - Raises `ValueError` for a non-`TRACK` element or more than
    `MAX_POSITION_MARKS` (256) children (defensive bound).
- `is_green(red, green, blue)`: exported so its rule is directly testable.
  Green channel must be `>= GREEN_MIN_LEVEL` (80) and exceed both red and
  blue by `>= GREEN_CHANNEL_MARGIN` (20). Raises `ValueError` for
  non-integer or out-of-range channels.
- `load_rekordbox_xml(data)` / `collection_tracks(root)`: optional
  convenience for parsing a full exported XML document already held in
  memory (`str` or `bytes`), read-only. `load_rekordbox_xml` rejects any
  `<!DOCTYPE`/`<!ENTITY` text before calling `ET.fromstring`, as a simple
  mitigation against entity-expansion attacks (XXE/billion-laughs) in
  stdlib `ElementTree` — this is a textual guard, not a full XML security
  audit. `collection_tracks` only returns `<TRACK>` elements under
  `<COLLECTION>`, explicitly excluding the Key-only `<TRACK>` references
  that appear under `<PLAYLISTS>` in a rekordbox export.

### Unresolved assumption: what counts as "green"

Rekordbox does not publish its memory/hot-cue color palette as a
stdlib-readable spec, and no primary evidence for an exact `(R, G, B)`
palette triple was established during this work. Rather than hardcode an
unverified exact color and risk silently misclassifying real exported
cues, `is_green()` uses a channel-dominance heuristic. **This is a
documented approximation, not a confirmed reverse-engineered constant.**
If a real exported XML's green swatch does not satisfy this rule (or a
different color incorrectly does), the threshold constants
(`GREEN_MIN_LEVEL`, `GREEN_CHANNEL_MARGIN`) are the place to revisit, ideally
against a real exported sample once available — no real library data was
used or is committed here.

## `djjev/three_band.py`

### Contract

`parse_three_band(data)` takes raw `bytes`/`bytearray` — the exact
contents of one Rekordbox ANLZ container (`.DAT`/`.EXT`) — and returns one
of:

- `{'status': 'unsupported', 'reason': ...}` — magic, declared header/tag
  lengths, entry byte width, or entry count are missing or inconsistent
  with the buffer. Reasons: `missing_anlz_file_magic`,
  `malformed_file_header`, `no_recognized_3band_tag`,
  `unexpected_tag_header_length`, `unexpected_entry_width`,
  `implausible_entry_count`, `entries_exceed_container`,
  `truncated_tag_header`, `tag_length_too_small`, `tag_exceeds_container`,
  `tag_header_length_out_of_bounds`.
- `{'status': 'ambiguous', 'reason': 'multiple_3band_tags_present',
  'formats_found': [...]}` — more than one recognized 3-band tag is
  present in the same buffer; the module does not pick one silently.
- `{'status': 'parsed', 'format', 'kind', 'entries', 'mid', 'high', 'low',
  'entry_order_assumption', 'declared_tag_header_bytes',
  'expected_entries_reference', 'limitations'}` — `mid`/`high`/`low` are
  equal-length lists of raw `0-255` integers, one per source column
  (preview) or per-source-second sample (detail), in source order.

Only the `PWV6` (color waveform **preview**, `0x14`-byte tag header,
reference count ~1200 columns) and `PWV7` (color waveform **detail**,
`0x18`-byte tag header, reference rate ~150 samples/source-second) tags are
recognized, per
https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/anlz.html.
Any other waveform/color tag (e.g. `PWAV`, `PWV3`–`PWV5`) is left
uninterpreted, but its own declared header/tag length is still
bounds-checked while scanning. Every tag's declared length is checked
against both its own container and the file's own declared length before
any byte is read. **A single malformed or truncated tag anywhere in the
buffer — recognized or not, before or after a valid 3-band tag — makes the
whole result `'unsupported'`**, rather than silently keeping an earlier
valid tag while masking corruption elsewhere in the container.

**These values are raw magnitude codes on Rekordbox's own undocumented
scale — not dBFS, not normalized amplitude, not post-EQ/post-fader output,
and not evidence of vocals, a drop, or any other arrangement section.**
The common display convention (blue=low, orange=mid, white=high) describes
Rekordbox's own rendering, not a calibration verified by this parser. When
comparing two tracks, compare their band series to each other; this module
does not itself compare, boost, or recommend anything.

### Unresolved assumption: per-entry byte order

Each entry is 3 raw bytes, taken as `(mid, high, low)` in that order,
following the wording of the cited reference. This byte order was **not**
independently re-derived here from a real captured `.DAT`/`.EXT` file — no
real analysis file was available or used in this checkout (synthetic
fixtures only). `entry_order_assumption` is returned in every parsed
result specifically so a caller can see, and if needed override or verify,
which assumption produced the labeled series.

### Unresolved assumption: reference entry counts

`expected_entries_reference` (1200 for `PWV6`, `None`/derivable as
`entries / 150` seconds for `PWV7`) is informational only. The parser does
**not** hard-fail when the container's own declared `entry_count` differs
from these reference figures, since the exact constant may be
version-dependent and only the file's own declared lengths are treated as
authoritative for bounds-checking.

## Validation run

The following ran against the **initial** version of both modules, before
the container-bounds and track-duration corrections described above:

```
$ python3 -m unittest tests.test_rekordbox_cues tests.test_three_band -v
...
Ran 46 tests in 0.002s
OK

$ python3 -m unittest discover -s tests -q
Ran 357 tests in 4.810s
OK (skipped=7)
```

The 7 skips are the pre-existing optional Essentia DSP fixtures
(`tests/test_audio_analysis.py`), unrelated to this work and unaffected by
it. No test in either new file touches the network, the filesystem beyond
synthetic in-memory fixtures, a live Rekordbox library, or real music/
library data; all XML and ANLZ inputs are constructed synthetically in the
test files themselves.

**Not yet executed:** a subsequent review found two correctness gaps —
`three_band.py` silently tolerating malformed/truncated trailing tag data,
and `rekordbox_cues.py` not checking a cue's `Start` against the track's own
`TotalTime` — which were then fixed, with matching new synthetic test cases
added for both (`tests/test_three_band.py`, `tests/test_rekordbox_cues.py`).
Those specific fixes and their new tests have **not** been re-run in this
checkout; only the numbers above are an executed result.

## What this is not

This is an **unconnected parser foundation**, not a feature already
controlling mixes. Nothing in `rekordbox_cues.py` or `three_band.py` is
imported by, called from, or wired into entry timing, transition decisions,
mixer/EQ control, or any other control path — that integration, and any
claim that green-cue-aware entry or 3-band-aware comparison is live during a
set, belongs to the owner of `entry_timing.py`, `audio_timeline.py`,
`policy.py` and the other integration/control files listed above.
