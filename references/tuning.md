# The planner, its constants, and other people's mixes

## How `plan.py` chooses the changes

**1. Where a change is possible.** For every frame and every pair of stages, `stagemix.match_frames` tries to lay one picture over the other using scale and shift only (no rotation). The frame becomes a candidate when all of this holds:

- the biggest matched face is the same member in both stages (identity similarity at least `SIM_SAME`); when the faces are too small for an identity vector, at least 4 faces of the formation must line up instead
- landmarks agree within 0.14 face heights, head tilt differs by at most 14 degrees
- the needed zoom is at most `MAX_ZOOM` and neither picture shows a border
- `WIN` frames before and after, the face has drifted by at most 0.45 face heights and neither broadcast cuts its camera
- the face is on screen at least `MIN_SEG` frames before the change in the old stage and after it in the new one

Each candidate gets a quality `q` from 0 to 1: tighter landmarks, less drift, less zoom, less tilt and no unmatched bystander faces score higher.

A second kind of change needs no face: at a frame where a stage's own director cuts to a new camera, the mix may switch to that stage's fresh shot.

**2. Which changes are used.** Dynamic programming picks the path with the best total over the song:

- a face-matched change is worth `1.25 * q`, more on a beat (most on a two-bar start, then bar start, even beat, any beat), minus `PER_SWITCH`
- a switch on a camera cut is worth little, so it is used when a shot would otherwise drag
- shots last `MIN_SHOT` to `MAX_SHOT` frames; under 1.5 s costs a little, over 4 s costs increasingly more
- never straight back to the stage just left
- 60 passes charge "rent" to over-used stages and keep the pass with the best reward for the most even screen time

**3. How a change is rendered.** Both pictures are zoomed and shifted so the face coincides, cross-dissolved over 2 frames (`q` of 0.5 or more) or 4 frames. Between changes the virtual camera eases from one alignment to the next, or snaps back unseen on the director's own cut.

**4. Which changes get a mask transition.** A face-matched change that lands on every `FX_EVERY`-th beat is rendered as a bloom (one matched face, on a bar start) or a sweep instead of the dissolve, when both faces stay on screen for its `FX_LEN` frames and no other mask transition is within `FX_GAP` frames. Bar starts choose first. The path from step 2 is not changed by this. `references/transitions.md` has the transitions and the reasons.

## Constants (top of `scripts/plan.py`)

| Constant | Value | Meaning |
|---|---|---|
| `MAX_ZOOM` | 1.6 | largest zoom used to bring two pictures into register |
| `WIN` | 3 | frames either side of a change that must be cut-free and tracked |
| `MIN_SHOT` | 28 | shortest shot, frames (about two beats) |
| `MAX_SHOT` | 210 | longest shot, frames (7 s) |
| `MIN_SEG` | 12 | frames a picture must stay up next to a change: no flash frames |
| `SIM_SAME` | 0.42 | identity similarity that counts as the same member |
| `PER_SWITCH` | 0.50 | fixed cost of any change |
| `FX_EVERY` | 2 | mask transitions on changes that hit every n-th beat: 2 is beats 1 and 3 of the bar, 4 bar starts only, 1 any beat, 0 none |
| `FX_GAP` | 30 | frames without a mask transition between two of them |
| `FX_LEN` | 10 | frames a sweep takes, and a bloom after the frame of the change |
| `FX_SOFT` | 0.15 | width of a sweep's soft edge, as a share of the way the edge travels |
| `FX_HOLD` | 3 | frames a bloom shows the new face alone before it spreads. They are part of `FX_LEN`: the spread gets the frames that are left, so leave it at least 5 |
| `FX_GLOW` | 90 | brightness added on the spreading edge of a bloom, out of 255; 0 is none |

These are judgment values, not derived ones. Only `PER_SWITCH` and the screen-time balance were tuned against results; the `FX_` values were set on the contact sheets of two songs, `FX_HOLD` from the drift of the faces measured on them (`references/transitions.md`).

| Complaint | Change |
|---|---|
| Too many changes, restless | raise `PER_SWITCH` (0.6 to 0.7) or `MIN_SHOT` |
| Too few changes, shots drag | lower `PER_SWITCH`; the over-4-s penalty is in `len_score` |
| Should hit the beat more | raise the weights in `beat_bonus` |
| A join shows two different members | raise `SIM_SAME` (0.5) |
| Faces jump at joins | demand more quality: the `q < 0.25` gate in `pair_candidates` (try 0.35), or the landmark gate `m["res"] > 0.14` |
| A wide shot is matched on one small face and everyone else doubles in the dissolve | raise the `m["main_size"] < 60` gate in `pair_candidates` (try 100), or the `q < 0.25` gate |
| Picture gets soft, or zooms in too far | lower `MAX_ZOOM` (1.3) |
| `verify.py` lists fast zooms or pans | raise `MIN_SHOT`, or lower `MAX_ZOOM` |
| One stage dominates | raise the `25 * spread` weight in `main` |
| Too many mask transitions, or none wanted | `FX_EVERY` 4 (bar starts only) or 0 (none); or raise `FX_GAP` |
| More mask transitions wanted | lower `FX_GAP` (15); `FX_EVERY` 1 |
| A mask transition looks slow | lower `FX_LEN` (8) |
| The edge of a sweep is too hard | raise `FX_SOFT` (0.25) |
| Many `bloom` rows are rejected because the new face has left the old head before the spread | lower `FX_HOLD` (2) |
| In a bloom the new face waits too long before the picture follows | lower `FX_HOLD` (2), then `FX_LEN` (8). The spread starts slowly on purpose: the exponent `2.2` in `bloom`, `render.py` |
| The opening of a bloom shows too much, or too little, around the face | the `0.36` in `bloom`, `render.py`: half the opening's width, in face heights |
| The light on the edge of a bloom is too strong, or not wanted | lower `FX_GLOW` (50), or 0 |
| A different mask transition wanted | `references/transitions.md` |

The values in brackets are untested starting points. After any change: plan again, render a draft, and look at the sheets.

`plan.py` keeps the matched frames in `work/candidates.npy` and rebuilds the file by itself when faces or cuts are newer. It cannot see an edited constant: after touching `SIM_SAME`, `MAX_ZOOM`, `WIN` or a gate inside `pair_candidates`, delete the file. The other constants need no deletion, and planning again takes seconds.

`render.py --dissolve N` forces N dissolve frames (even) at every matched change, mask transitions included: `--dissolve 4` is the quick way to see a plan without them, with every dissolve 4 frames long. For exactly the same edit without mask transitions, set `FX_EVERY = 0`, plan into a second EDL file, and set it back. `--from SEC --to SEC` renders part of the song for a quick look; `sheet.py` and `verify.py` need a full render.

## What human editors do

From tutorials (Premiere workflows) and from three published mixes of one song, reverse-engineered with `analyze_mix.py`:

- Stack all stages, sync them by audio waveform, keep one audio track. Never ripple-delete: total length must not change.
- The outfit change itself: lower the opacity of the upper layer, move and scale it until the same member's face coincides, add a very short dissolve, then keyframe back to full frame.
- Keep the camera movement continuous across a join (a pull-out continues as a pull-out).

| Mix | Stage switches | Median shot | Style |
|---|---|---|---|
| A | 62 | 3.5 s | face match, then hard cut or a 1 to 3 frame dissolve (at 60 fps); a few long dissolves |
| B | 209 | 0.9 s | a switch about every two beats, mostly hard cuts, almost no zoom |
| C | 77 | 2.3 s | in between |

- Zoom to match faces: usually 1.1 to 1.4, at most 1.9.
- All three borrow the director's own camera cuts for part of their switches; mix A for about 40 %. This is a normal technique, not a failure of face matching.
- Cut times are spread almost evenly against the beat: editors follow movement and composition. Hence the beat is a bonus here, not a rule.
- All three use the live audio of one stage; this pipeline uses the studio track.

## Studying or comparing other mixes

Only when the user asks for it. Never use another mix's cut list to plan.

```bash
# a mix of the same song, saved without two leading digits; 720p is enough for analysis
$PY -I $S/prep.py work research/mix_a.mp4
$PY -I $S/sync.py work/audio/00_studio_audio.wav work/sync.json work/audio/mix_a.wav
# which stage is on screen when, where it switches, dissolve lengths, zooms (minutes per mix: run in the background)
$PY -I $S/analyze_mix.py work/sync.json work/lowres mix_a FPS_OF_THE_MIX work/mix_a.json
# side by side with your finished mix, every cell at the same moment of the song (use 1080p copies)
$PY -I $S/compare.py work/sync.json work/edl.json ../output/compare.mp4 ../output/MINE.mp4 research/mix_a.mp4
```

`FPS_OF_THE_MIX` is in `work/lowres/fps.json`. A high `unmatched frames` share means the mix uses stages you do not have, or heavy effects.

When presenting a comparison, tell the user:

- the other mixes' intros are cut off, and their cells go black when they end before the studio track does
- other mixes are often 60 fps; this pipeline's 29.97 fps looks less fluid next to them
- the dance can differ by up to about 0.1 s between cells: other editors do not correct the broadcasters' picture delay. It is not an alignment error.

## Limits to state when relevant

- Alignment is scale and shift only. Candidates whose head tilt differs too much are dropped rather than rotated.
- The stages are synchronised to each other within a fraction of a frame; the absolute lip sync against the studio track can be off by a few tens of milliseconds and cannot be measured.
- Output is 29.97 fps even when some sources are 60 fps.
