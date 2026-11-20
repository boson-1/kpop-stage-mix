"""Reverse-engineer somebody else's stage mix against the original stage videos.

For every frame of the mix we look up, in each source stage, the frame playing
at the same moment of the song and ask "is the mix frame a (zoomed / shifted)
copy of this one?".  That yields the edit decision list: which stage is on
screen when, where the editor switches, how long each dissolve lasts and how
much the picture was zoomed or moved to line the performer up.

usage: analyze_mix.py SYNC.json LOWRES_DIR MIX_NAME FPS_MIX OUT.json
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

W, H = 128, 72
SCALES = [1.0, 1.04, 1.08, 1.12, 1.16, 1.2, 1.25, 1.3, 1.36, 1.42, 1.5, 1.6, 1.75, 1.9]
PURE = 0.62      # a frame counts as "purely this source" above this score ...
MARGIN = 0.15    # ... when it also beats the runner-up source by this much


def load_raw(path):
    return np.memmap(path, dtype=np.uint8, mode="r").reshape(-1, H, W)


def match(mix_f, src_f):
    """Best (score, scale, dx, dy) of mix frame as a zoomed crop of the source frame."""
    best = (-1.0, 1.0, 0.0, 0.0)
    for s in SCALES:
        tw, th = int(round(W / s)), int(round(H / s))
        t = cv2.resize(mix_f, (tw, th), interpolation=cv2.INTER_AREA) if s != 1.0 else mix_f
        r = cv2.matchTemplate(src_f, t, cv2.TM_CCOEFF_NORMED)
        _, v, _, loc = cv2.minMaxLoc(r)
        if v > best[0]:
            # offset of the crop centre from the source centre, in fractions of frame size
            best = (float(v), s, (loc[0] + tw / 2 - W / 2) / W, (loc[1] + th / 2 - H / 2) / H)
        if s == 1.0 and v > 0.97:
            break
    return best


def main():
    sync = json.loads(Path(sys.argv[1]).read_text())
    low = Path(sys.argv[2])
    mix_name, fps_mix, out = sys.argv[3], float(sys.argv[4]), sys.argv[5]
    clips = sync["clips"]
    names = sorted(n for n in clips if n[:2].isdigit())
    srcs = {n: load_raw(low / f"{n}.raw") for n in names}
    src_fps = json.loads((low / "fps.json").read_text())     # native frame rates, written by prep.py
    mix = load_raw(low / f"{mix_name}.raw")
    off_mix = clips[mix_name]["offset"]

    n = len(mix)

    def match_all(i, dt):
        """Score mix frame i against every source at the same song time (+-1 source frame)."""
        tr = i / fps_mix - off_mix + dt
        sc_row, par_row = np.zeros(len(names), np.float32), np.zeros((len(names), 3), np.float32)
        for k, name in enumerate(names):
            fps = src_fps[name]
            j0 = int(round((tr + clips[name]["offset"]) * fps))
            for j in (j0 - 1, j0, j0 + 1):
                if 0 <= j < len(srcs[name]):
                    sc, s, dx, dy = match(mix[i], srcs[name][j])
                    if sc > sc_row[k]:
                        sc_row[k], par_row[k] = sc, (s, dx, dy)
        return sc_row, par_row

    # editors rarely keep picture and sound sample-aligned: find the mix's own video delay first
    probe = np.linspace(n * 0.1, n * 0.9, 240).astype(int)
    dts = np.arange(-0.20, 0.2001, 1 / 30)
    dt = float(dts[int(np.argmax([np.mean([match_all(i, d)[0].max() for i in probe]) for d in dts]))])
    print(f"  mix video runs {dt*1000:+.0f} ms against its own audio", flush=True)

    best_src = np.full(n, -1, int)
    scores = np.zeros((n, len(names)), np.float32)
    params = np.zeros((n, len(names), 3), np.float32)
    for i in range(n):
        scores[i], params[i] = match_all(i, dt)
        best_src[i] = int(np.argmax(scores[i]))
        if i % 2000 == 0:
            print(f"  frame {i}/{n}", flush=True)

    # runs of "pure" frames belonging to one source
    srt = np.sort(scores, axis=1)
    pure = (srt[:, -1] >= PURE) & (srt[:, -1] - srt[:, -2] >= MARGIN)
    runs = []
    i = 0
    while i < n:
        if not pure[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and pure[j + 1] and best_src[j + 1] == best_src[i]:
            j += 1
        runs.append([i, j, int(best_src[i])])
        i = j + 1
    # drop blips shorter than 4 frames, then merge neighbours of the same source
    runs = [r for r in runs if r[1] - r[0] + 1 >= 4]
    merged = []
    for r in runs:
        if merged and merged[-1][2] == r[2] and r[0] - merged[-1][1] <= int(fps_mix * 0.5):
            merged[-1][1] = r[1]
        else:
            merged.append(r)

    trans = []
    for a, b in zip(merged, merged[1:]):
        gap = b[0] - a[1] - 1                           # frames that are neither pure A nor pure B
        t_song = ((a[1] + b[0]) / 2 + 0.5) / fps_mix - off_mix
        sa, sb = params[a[1], a[2]], params[b[0], b[2]]
        trans.append({
            "t_song": round(float(t_song), 3), "t_mix": round((a[1] + b[0] + 1) / 2 / fps_mix, 3),
            "from": names[a[2]], "to": names[b[2]],
            "blend_frames": int(gap), "blend_ms": round(gap / fps_mix * 1000),
            "out_scale": round(float(sa[0]), 2), "out_shift": [round(float(sa[1]), 3), round(float(sa[2]), 3)],
            "in_scale": round(float(sb[0]), 2), "in_shift": [round(float(sb[1]), 3), round(float(sb[2]), 3)],
            "mix_frame_out": int(a[1]), "mix_frame_in": int(b[0]),
        })
    shots = [{"start_mix": round(r[0] / fps_mix, 3), "end_mix": round((r[1] + 1) / fps_mix, 3),
              "src": names[r[2]],
              "scale_med": round(float(np.median(params[r[0]:r[1] + 1, r[2], 0])), 2),
              "scale_start": round(float(params[r[0], r[2], 0]), 2),
              "scale_end": round(float(params[r[1], r[2], 0]), 2)} for r in merged]
    unknown = float((~pure).mean())
    Path(out).write_text(json.dumps({"mix": mix_name, "fps": fps_mix, "offset": off_mix, "video_delay": dt,
                                     "unmatched_ratio": unknown, "shots": shots, "transitions": trans}, indent=1))
    np.savez_compressed(Path(out).with_suffix(".npz"), scores=scores, params=params, best=best_src)

    durs = np.array([s["end_mix"] - s["start_mix"] for s in shots])
    blends = np.array([t["blend_frames"] for t in trans])
    print(f"{mix_name}: {len(shots)} shots, {len(trans)} stage switches, unmatched frames {unknown:.1%}")
    print(f"  shot length s: median {np.median(durs):.2f}  mean {durs.mean():.2f}  min {durs.min():.2f}  max {durs.max():.2f}")
    print(f"  blend frames histogram (@{fps_mix:.0f}fps): " +
          ", ".join(f"{v}f x{c}" for v, c in zip(*np.unique(blends, return_counts=True))))
    use = {}
    for s in shots:
        use[s["src"]] = use.get(s["src"], 0) + s["end_mix"] - s["start_mix"]
    print("  screen time per stage: " + ", ".join(f"{k[:2]}:{v:.0f}s" for k, v in sorted(use.items())))


if __name__ == "__main__":
    main()
