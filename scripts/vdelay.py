"""Measure how far each broadcast's picture runs ahead of / behind its own sound.

Every station delays picture and sound differently (measured: up to ~0.2 s
between two shows).  Audio sync alone therefore leaves the choreography a few
frames apart between stages, which shows as a stutter on a cut.  The dancers move
on the beat in every stage, so the frame-difference "motion energy" of two stages
correlates; the lag of that correlation is the relative picture delay.

Adds "vdelay" (seconds, relative to the average stage) to every source in SYNC.json.

usage: vdelay.py SYNC.json LOWRES_DIR
"""
import json
import sys
from pathlib import Path

import numpy as np

W, H = 128, 72
RATE = 120.0                 # Hz the motion signals are resampled to
MAX_LAG = 0.25               # seconds


def motion_signal(raw_path, fps, offset, dur):
    raw = np.memmap(raw_path, dtype=np.uint8, mode="r").reshape(-1, H, W)
    j0, j1 = max(0, int(offset * fps) - 2), int((offset + dur) * fps) + 2   # a clip may start after the song did
    fr = raw[j0:j1].astype(np.float32)
    d = np.abs(fr[1:] - fr[:-1]).mean((1, 2))
    d = np.log1p(np.minimum(d, 4 * np.median(d)))            # camera cuts must not dominate
    t_d = (np.arange(j0, j1 - 1) + 0.5) / fps - offset       # song time of each difference
    s = np.interp(np.arange(0, dur, 1 / RATE), t_d, d)
    k = int(RATE)
    s -= np.convolve(s, np.ones(k) / k, mode="same")
    return (s - s.mean()) / s.std()


def solve(sig, names, lo, hi):
    """Least-squares per-source delay (ms) from all pairwise lags inside song time [lo, hi]."""
    L, a0, a1 = int(MAX_LAG * RATE), int(lo * RATE), int(hi * RATE)
    rows, lags = [], []
    for a, na in enumerate(names):
        for b, nb in enumerate(names):
            if a == b:
                continue
            x, y = sig[na][a0:a1], sig[nb][a0:a1]
            cc = [np.mean(x[max(0, l):len(x) + min(0, l)] * y[max(0, -l):len(y) + min(0, -l)])
                  for l in range(-L, L + 1)]
            r = np.zeros(len(names))
            r[a], r[b] = 1, -1
            rows.append(r)
            lags.append((int(np.argmax(cc)) - L) / RATE * 1000)
    rows.append(np.ones(len(names)))                          # delays sum to zero
    lags.append(0)
    return np.linalg.lstsq(np.array(rows), np.array(lags), rcond=None)[0]


def main():
    sync_path, low = Path(sys.argv[1]), Path(sys.argv[2])
    sync = json.loads(sync_path.read_text())
    clips, dur = sync["clips"], sync["ref_dur"]
    names = sorted(n for n in clips if n[:2].isdigit())
    fps = json.loads((low / "fps.json").read_text())         # native frame rates, written by prep.py
    sig = {n: motion_signal(low / f"{n}.raw", fps[n], clips[n]["offset"], dur) for n in names}
    edges = np.linspace(8, dur - 4, 5)                        # four independent quarters of the song
    parts = np.array([solve(sig, names, a, b) for a, b in zip(edges, edges[1:])])
    for i, n in enumerate(names):
        clips[n]["vdelay"] = round(float(parts[:, i].mean()) / 1000, 4)
        print(f"{n:18s} picture delay {parts[:, i].mean():+5.0f} ms  (quarters: "
              + " ".join(f"{v:+4.0f}" for v in parts[:, i]) + f", sd {parts[:, i].std():.0f})")
    sync_path.write_text(json.dumps(sync, indent=1))


if __name__ == "__main__":
    main()
