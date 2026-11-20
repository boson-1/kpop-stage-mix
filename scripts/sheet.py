"""Contact sheets of the stage changes in a full-length render, for eyeballing the joins.

Each row is one change: frames -3 -2 -1 | 0 +1 +3 around the first frame of the new stage.
Label:  #shot  time in the video  from>to stage  kind, and for a face-matched change
  q     how invisible the plan expects the join to be, 0..1
  res   landmark mismatch after alignment, in face heights
  dr    how far the two faces drift apart 3 frames away, in face heights
  z     zoom of the outgoing > incoming picture at the join
  sim   identity similarity of the matched face (-- when the faces are too small to tell)
  px    height of the matched face on screen
  beat  beat reward (0 = off the beat)

The last argument picks the rows: match (default), cut, all, or shot numbers such as 7,22,58
for a closer look at those joins on large tiles.

usage: sheet.py EDL.json VIDEO OUT_PREFIX [match|cut|all|N,N,...]
"""
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

OFFS = (-3, -2, -1, 0, 1, 3)


def frames(video, size, fps, start=None, n=None):
    """Frames of the video as BGR at `size`; start / n pick frames by index."""
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, (start - 0.5) / fps):.5f}"]
    cmd += ["-i", video]
    if n is not None:
        cmd += ["-frames:v", str(n)]
    cmd += ["-vf", f"scale={size[0]}:{size[1]}:flags=area", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.uint8).reshape(-1, size[1], size[0], 3)


def main():
    edl = json.loads(Path(sys.argv[1]).read_text())
    video, prefix = sys.argv[2], sys.argv[3]
    Path(prefix).parent.mkdir(parents=True, exist_ok=True)       # imwrite fails silently on a missing folder
    which = sys.argv[4] if len(sys.argv) > 4 else "match"
    picked = {int(x) for x in which.split(",")} if which[0].isdigit() else None
    size, per_page = ((640, 360), 4) if picked else ((320, 180), 10)
    big = size[0] // 320                                         # text scale
    fps, shots = edl["fps"], edl["shots"]
    whole = None if picked else frames(video, size, fps)
    rows = []
    for i, s in enumerate(shots):
        if i == 0 or (i not in picked if picked else which != "all" and s["kind"] != which):
            continue
        k = s["start"] - edl["f0"]
        first = max(k + OFFS[0], 0)
        fr = frames(video, size, fps, first, OFFS[-1] - OFFS[0] + 1) if picked else whole[first:]
        tiles = []
        for o in OFFS:
            t = fr[min(max(k + o - first, 0), len(fr) - 1)].copy()
            cv2.putText(t, f"{o:+d}", (4, size[1] - 6), 0, 0.5 * big, (0, 255, 0), big)
            tiles.append(t)
        lab = f"#{i} {int(k / fps // 60)}:{k / fps % 60:04.1f} {shots[i - 1]['src'][:2]}>{s['src'][:2]} {s['kind']}"
        if s["kind"] == "match":
            sim = "--" if s["sim"] is None else f"{s['sim']:.2f}"
            lab += (f" q{s['q']:.2f} res{s['res']:.2f} dr{s['drift']:.2f} z{s['cam_out']['z']:.2f}>{s['cam_in']['z']:.2f}"
                    f" sim{sim} px{s['face_px']} beat{s['beat']:.2f}")
        row = np.hstack(tiles)
        cv2.rectangle(row, (0, 0), (min(row.shape[1], big * (7 * len(lab) + 6)), 16 * big), (0, 0, 0), -1)
        cv2.putText(row, lab, (3, 12 * big), 0, 0.4 * big, (0, 255, 255), big)
        rows.append(row)
    for p in range(0, len(rows), per_page):
        out = f"{prefix}_{p // per_page:02d}.jpg"
        cv2.imwrite(out, np.vstack(rows[p:p + per_page]), [cv2.IMWRITE_JPEG_QUALITY, 82])
        print(out)


if __name__ == "__main__":
    main()
