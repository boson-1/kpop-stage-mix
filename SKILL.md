---
name: kpop-stage-mix
description: Use when asked to make, cut, redo or fix a K-pop stage mix / 一鍵換裝 / 一键换装 / 換裝混剪 / 교차편집 / ステージミックス / 交差編集 (outfit-change edit - one song assembled from several music-show stages / 打歌舞台 so the outfit changes on a face-matched cut), or to reverse-engineer or compare existing stage mixes. Not for edits of a single video such as highlights, fancam cuts or subtitles.
---

# K-pop Stage Mix (一鍵換裝 / 교차편집)

## Overview

A stage mix plays one song straight through while the picture jumps between broadcast stages. At each jump the same member's face sits at the same place and size, so only the outfit and the set change. Most jumps are a dissolve of a few frames; those on a strong beat get a mask transition, an iris opening out of the face or a soft edge sweeping over the frame. The scripts in `scripts/` build the whole edit from the downloaded stages. Judgment is needed for four things: choosing sources, proving the stages are in sync, looking at every join, and reporting honestly.

**Core principle:** after `conform.py`, frame *f* is the same instant of the choreography in every stage. Every later step addresses frames by index and trusts this. Audio sync alone does not deliver it, so the picture sync is measured (step 7), never assumed.

Output: 1920x1080, 29.97 fps, H.264, with the studio track as sound. A four-stage song takes about half an hour end to end.

## Setup

- Needs `ffmpeg`, `ffprobe`, `yt-dlp`, Python 3.10+, Linux or macOS. An NVIDIA GPU is strongly recommended: face detection takes about 6 ms per frame on it instead of about 100 ms on a CPU, and four stages run in parallel finish in about 5 minutes.
- `S` is the `scripts/` directory next to this file. `PY` is the Python of a virtualenv with the requirements installed. Reuse a `.venv` in the project folder if there is one; otherwise:

```bash
uv venv && uv pip install -r $S/requirements.txt
# NVIDIA GPU: swap the CPU runtime for the GPU one (the two cannot be installed side by side; several GB)
uv pip uninstall onnxruntime && uv pip install "onnxruntime-gpu[cuda,cudnn]"
```

- One folder per song: `<song>/sources/` for downloads, `<song>/work/` for intermediates (about 1 GB per stage), finished videos in `output/` beside the song folders. Run everything from `<song>/`.
- The docstring at the top of each script is its manual.

## Workflow

Run the steps in order. Do not go past a step whose gate (table below) is not met.

```bash
# 1  collect stages: read references/sourcing.md first
#    sources/NN_show_MMDD.mp4 (NN = 01, 02, ...), sources/00_studio_audio.m4a, and the list sources.tsv
# 2  analysis caches and frame rates
$PY -I $S/prep.py work sources/*
# 3  audio offset of every stage against the studio track
$PY -I $S/sync.py work/audio/00_studio_audio.wav work/sync.json work/audio/*.wav
# 4  first guess of each broadcaster's picture delay
$PY -I $S/vdelay.py work/sync.json work/lowres
# 5  one 1080p / 29.97 fps file per stage on the song timeline (frame 30 = song time 0)
$PY -I $S/conform.py work/sync.json sources work/conf
# 6  camera cuts, and faces (one process per stage, 4 at a time on one GPU)
mkdir -p work/logs
$PY -I $S/shots.py work/conf work/shots.json &
ls work/conf | sed 's/\.mp4$//' | xargs -P 4 -I{} sh -c "$PY -I $S/faces.py work/conf work/faces {} > work/logs/faces_{}.log 2>&1"
wait
tail -qn 1 work/logs/faces_*.log        # every line must read "NAME: done, N frames, M faces"
# 7  picture sync between stages, measured from face trajectories
$PY -I $S/check_sync.py work
# 8  beat grid of the studio track
$PY -I $S/beats.py work/audio/00_studio_audio.wav work/beats.json
# 9  edit decision list
$PY -I $S/plan.py work work/edl.json
# 10 draft render, a contact sheet row for every change, automatic checks
$PY -I $S/render.py work work/edl.json sources/00_studio_audio.m4a work/draft.mp4 --preset veryfast --crf 18
$PY -I $S/sheet.py work/edl.json work/draft.mp4 work/sheets/draft all
$PY -I $S/verify.py work work/edl.json work/draft.mp4
# 11 final render (x264 slow, CRF 16) of the plan you reviewed, and the checks again on that file
mkdir -p ../output
$PY -I $S/render.py work work/edl.json sources/00_studio_audio.m4a ../output/<Group>_<Song>_outfit_change_mix.mp4
$PY -I $S/verify.py work work/edl.json ../output/<Group>_<Song>_outfit_change_mix.mp4
```

| Step | Gate | When it is not met |
|---|---|---|
| 2 prep | every stage listed as about 1920x1080 at 29.97 or 59.94 fps, no `WARNING` | A warning means picture and sound do not start together inside the file: get another copy of that stage. |
| 3 sync | `confident windows` close to all, `local lag` within about ±15 ms | Few confident windows over the whole song: live vocals or a band over the track; the offset is usually still right and step 7 will confirm it. Weak windows in one stretch only, or a lag that steps: the arrangement differs (dance break, intro, short version). Drop that stage. All stages weak: wrong studio track. |
| 5 conform | one line per stage, same frame count for all | |
| 6 faces | every log ends in `done`, and says `face models run on CUDAExecutionProvider` when there is a GPU | A stage that died is simply run again. `CUDA failure 999` (seen repeatedly under WSL2) takes down all running face jobs at once and can need two or three tries. Never continue with a missing or old `.npz`. |
| 7 check_sync | every stage within ±0.3 frame, `fit residual rms` about 0.2 or less, no stage `NOT MEASURED` | Do the correction round below. Expect to need it once. |
| 8 beats | plausible BPM, phase per quarter within ±25 ms | A drifting grid means a tempo change: beat rewards will be wrong in part of the song. Say so in the report. |
| 9 plan | prints shots, changes, screen share, match quality, mask transitions | `no stage can open` / `no edit path`: the message names a `START_SEC` that only cuts the track's leading silence. Run `plan.py work work/edl.json START_SEC` with it. |
| 10 draft | `verify.py` ends with `all checks passed`, and you have looked at every sheet | Fix the cause, render again, check again. |

`vdelay.py` (step 4) has no gate: it is only the starting point for step 7, and a poor one with few stages.

### Correction round for step 7

```bash
$PY -I $S/check_sync.py work --apply          # rewrites the picture delays in sync.json, prints REDO="..."
REDO="..."                                    # paste the line it printed
$PY -I $S/conform.py work/sync.json sources work/conf $REDO
$PY -I $S/shots.py work/conf work/shots.json &
printf '%s\n' $REDO | xargs -P 4 -I{} sh -c "$PY -I $S/faces.py work/conf work/faces {} > work/logs/faces_{}.log 2>&1"
wait
tail -qn 1 work/logs/faces_*.log              # all "done"
$PY -I $S/check_sync.py work                  # the gate again
```

- Run `--apply` only while the gate is not met. Once every stage is within ±0.3 frame, go on.
- `--apply` changes the number of every stage, but only the stages in `REDO` get a new file. `conform.py` can shift by whole source frames only, and the line `will remain` is that rounding.
- When `--apply` answers that only rounding is left, the sync is as good as it can get: go on, and put the remaining offsets in the report.
- `check_sync.py` and `plan.py` refuse to run on faces or cuts older than the conformed video they belong to.

## Looking at the joins

Open every `work/sheets/draft_*.jpg` as an image. One row is one change: frames −3 −2 −1 | 0 +1 +3 around the first frame of the new stage. The label gives shot number, time in the video, stages, kind and the plan's numbers (explained at the top of `sheet.py`).

Reject a join when:

- the two sides show different members (`sim` under about 0.5 deserves a second look)
- the face visibly jumps or changes size
- a frame belongs to neither shot, or an edge of the picture shows
- the rest of the picture doubles badly during the dissolve, typically a wide shot matched on one small face (`px` under about 100)

A `cut` row only has to show two steady pictures: the planner switches there because the new stage's own director cuts at that frame.

A row labelled `iris` or `sweep` is a mask transition. Its tiles run from the frame before the transition to the frame after it, with the frame offset on each tile, and a soft edge separates the old stage from the new one: a circle growing out of the face, or a straight edge. Bodies and bystanders that do not continue across the edge are normal, because only the matched face is in register. Reject the row when:

- on tile 0, where the edge passes over the matched face, the face reads as two faces or as two halves that do not fit
- the member inside the new picture is not the one who stood there in the old picture
- a tile shows a third picture, or an edge of the picture

For a closer look at doubtful joins, and always at the five weakest that `verify.py` names: `$PY -I $S/sheet.py work/edl.json work/draft.mp4 work/sheets/close 58,54,6` (shot numbers, large tiles).

Rejected `match` and `cut` rows come first: change the planner's constants and plan again (`references/tuning.md`). Frames and cameras in the EDL are never edited by hand. Then every rejected `iris` or `sweep` row: for row `#N`, delete the `"fx"` entry of `shots[N]` in `work/edl.json`. This is the one hand edit there is; the change is then rendered as its dissolve, and its new row is judged like any dissolve. Planning again writes a new EDL with new shot numbers and every `"fx"` back, so the review starts over. Delete `work/sheets/` and repeat step 10. A join that is only borderline stays in, and goes on the list for the user.

## Reporting

You have only seen stills. The report must contain:

- the file with its resolution, frame rate, duration and size
- the stages used (show, date, video id from `sources.tsv`), and the ones left out with the reason
- the plan's numbers: shots, face-matched changes, mask transitions (iris and sweep as `plan.py` prints them, and how many of those you turned back into dissolves), camera-cut switches, median shot length, share per stage, changes on the beat
- what was checked: the `verify.py` lines of the final file, the remaining sync offsets, and that every join was inspected on the sheets
- what was not: motion, pacing and sound were never played. Ask the user to watch it once.
- the `to watch` list from `verify.py` and your own borderline joins, with times
- what is inherent: broadcaster logos and captions jump with the stage and move when a shot is zoomed; during a mask transition only the matched face continues across the edge, bodies and bystanders do not; the mix starts after the track's leading silence when `START_SEC` was used; the last shot is held for a second and fades out

## Pitfalls

Each of these was found by measurement and costs hours when rediscovered.

| Symptom or temptation | Fact | What to do |
|---|---|---|
| Audio offsets are perfect, yet the dance stutters by a few frames on a change | Every broadcaster delays picture against sound differently (measured −93 to +97 ms between shows) | Steps 4 and 7 for every new set of sources |
| Correcting sync by hand, adding the measured lag to `vdelay` again and again | `conform.py` shifts by whole source frames, so a hand correction oscillates around the rounding step | Only `check_sync.py --apply` |
| Adding an RGB step (filters, PNG frames, OpenCV colour images) to the render | An RGB round trip shifted the colours and clipped saturated stage lighting | `render.py` stays in YUV 4:2:0 from decoder to encoder. Keep it that way; `verify.py` reports the level difference |
| Camera cuts detected where nothing changed | Strobe lighting looks like a cut | Already handled: `stagemix.Source` drops cuts across which the faces stay put. Do not lower the thresholds in `shots.py` |
| A stage shows one very long "shot" | Broadcast dissolves are not detected as cuts | Harmless: a face match also needs the face to have been on screen for a while |
| The planner cannot start | Clip begins after the song's first sample; `conform.py` holds its first frame and `plan.py` refuses those frames | The `START_SEC` that `plan.py` suggests |
| A "new" stage that looks oddly familiar | Re-run programmes re-air old stages under a new date, fan "AI upscale" uploads look current | `references/sourcing.md` |
| A stage is missing from the mix or barely used | Its faces rarely line up with the others (wide shots, different blocking) | Report it; do not force it in |
| Making the mix fancier with longer dissolves, or with a transition that mixes both pictures over the whole frame | The stages are in register only at the matched face, and only around the frame of the change. An 8-frame luma dissolve doubled the faces on 3 of 4 changes | Use the mask transitions that exist; before adding one read `references/transitions.md` |
| Borrowing cut points from someone else's mix of the song | The planner needs none: it works from the stages alone | Use `analyze_mix.py` / `compare.py` only when the user asks to study or compare other mixes |

## References

- `references/sourcing.md`: finding, filtering, naming and downloading stages and the studio track. Read before step 1.
- `references/tuning.md`: how the planner chooses, every constant and what to change for a given complaint, what human editors do, and the tools for studying or comparing other mixes. Read before changing a constant or when the user asks about someone else's mix.
- `references/transitions.md`: the mask transitions and where the planner puts them, the film and television techniques that were weighed and why most were left out, and what a new transition must satisfy. Read when the user asks for another transition look, or before adding one.
