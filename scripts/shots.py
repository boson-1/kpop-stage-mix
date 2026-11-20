"""Find the broadcast director's own camera cuts in every conformed stage video.

An outfit-change transition must not straddle one of these, and switching stage
exactly on one is free (the viewer expects a new picture there anyway).

usage: shots.py CONF_DIR OUT.json
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

W, H = 160, 90


def lowres(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={W}:{H}:flags=area",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


def cuts(fr):
    """Frame indices that start a new shot."""
    g = fr.astype(np.float32).mean(3)
    d = np.abs(g[1:] - g[:-1]).mean((1, 2))                       # pixel change
    # colour histogram change: insensitive to fast dance motion, sensitive to a new picture
    q = (fr // 32).astype(np.int32)
    code = q[..., 0] * 64 + q[..., 1] * 8 + q[..., 2]
    hist = np.stack([np.bincount(c.ravel(), minlength=512) for c in code]).astype(np.float32) / (W * H)
    hd = np.abs(hist[1:] - hist[:-1]).sum(1)
    out = []
    for i in range(len(d)):
        lo, hi = max(0, i - 8), min(len(d), i + 9)
        nd = np.median(np.delete(d[lo:hi], i - lo))
        nh = np.median(np.delete(hd[lo:hi], i - lo))
        if d[i] > 3.0 * nd + 4 and d[i] > 12 and hd[i] > 2.0 * nh + 0.15:
            out.append(i + 1)
    return out


def main():
    conf, out = Path(sys.argv[1]), Path(sys.argv[2])
    res = {}
    for p in sorted(conf.glob("*.mp4")):
        fr = lowres(p)
        c = cuts(fr)
        res[p.stem] = {"n_frames": int(len(fr)), "cuts": c}
        gaps = np.diff([0] + c + [len(fr)]) / 29.97
        print(f"{p.stem}: {len(fr)} frames, {len(c)} cuts, median shot {np.median(gaps):.2f}s, longest {gaps.max():.1f}s")
    out.write_text(json.dumps(res))


if __name__ == "__main__":
    main()
