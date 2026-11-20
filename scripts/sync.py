"""Audio-sync every clip to a reference track (the studio audio).

Stage performances play the same backing track straight through, so one global
offset per clip is enough: clip_time = ref_time + offset.  The offset comes from
a single full-length cross-correlation; short windows then re-check it locally
so a clip whose arrangement differs (dance break, cut) shows up in the report.

An existing OUT.json is updated, not replaced: clips not named keep their entry, and a
picture delay ("vdelay") already measured for a clip survives.

usage: sync.py REF.wav OUT.json CLIP.wav [CLIP.wav ...]
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, correlate, sosfilt

SR = 22050
WIN = 4.0          # seconds per verification window
MAX_LAG = 0.25     # seconds searched around the global offset


def load(path):
    x, sr = sf.read(path, dtype="float32")
    assert sr == SR, (path, sr)
    sos = butter(4, [150, 6000], btype="band", fs=SR, output="sos")
    return sosfilt(sos, x).astype(np.float32)


def global_offset(ref, clip):
    cc = correlate(clip, ref, mode="full", method="fft")
    return (int(np.argmax(cc)) - (len(ref) - 1)) / SR


def local_check(ref, clip, offset):
    """Per window: (ref_time, lag relative to the global offset, normalised correlation)."""
    n, m = int(WIN * SR), int(MAX_LAG * SR)
    rows = []
    for start in range(0, len(ref) - n, n):
        c0 = start + int(round(offset * SR)) - m
        if c0 < 0 or c0 + n + 2 * m > len(clip):
            continue
        w, seg = ref[start:start + n], clip[c0:c0 + n + 2 * m]
        cc = correlate(seg, w, mode="valid", method="fft")
        e = np.sqrt(np.convolve(seg.astype(np.float64) ** 2, np.ones(n), mode="valid"))
        ncc = cc / (e * np.linalg.norm(w) + 1e-9)
        k = int(np.argmax(ncc))
        rows.append((start / SR, (k - m) / SR, float(ncc[k])))
    return rows


def main():
    ref_path, out_path, clips = sys.argv[1], sys.argv[2], sys.argv[3:]
    ref = load(ref_path)
    result = {}
    for c in clips:
        if Path(c).stem == Path(ref_path).stem:
            continue                                   # the reference itself: lets the caller pass audio/*.wav
        clip = load(c)
        off = global_offset(ref, clip)
        rows = local_check(ref, clip, off)
        good = [r for r in rows if r[2] >= 0.3]
        lags = np.array([r[1] for r in good]) if good else np.zeros(1)
        name = Path(c).stem
        result[name] = {"offset": off, "clip_dur": len(clip) / SR,
                        "windows_ok": len(good), "windows": len(rows),
                        "lag_min": float(lags.min()), "lag_max": float(lags.max())}
        weak = [f"{r[0]:.0f}" for r in rows if r[2] < 0.3]
        print(f"{name}: offset {off:+.4f}s  confident windows {len(good)}/{len(rows)}  "
              f"local lag {lags.min()*1000:+.0f}..{lags.max()*1000:+.0f} ms  weak@ref[{','.join(weak)}]")
    out = Path(out_path)
    data = json.loads(out.read_text()) if out.exists() else {"clips": {}}
    data.update(ref=Path(ref_path).stem, ref_dur=len(ref) / SR)
    for name, r in result.items():
        data["clips"].setdefault(name, {}).update(r)
    out.write_text(json.dumps(data, indent=1))


if __name__ == "__main__":
    main()
