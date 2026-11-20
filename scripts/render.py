"""Render the outfit-change mix described by an EDL.

Every shot is seen through a "virtual camera" (zoom + shift, never showing a
border).  At a face-matched change the outgoing and incoming cameras are the pair
solved in plan.py, so the member's face sits at the same place and size in both
pictures while they cross-dissolve.  Between changes the camera eases back, or
snaps back invisibly on the broadcast director's own camera cut.

Pictures stay in their native YUV 4:2:0 from decoder to encoder: going through RGB
clips the saturated stage lighting and resamples the colour planes twice.

A full render holds the last shot 26 frames past the end of the song and fades it to black.

usage: render.py WORK_DIR EDL.json AUDIO OUT.mp4 [--preset slow] [--crf 16] [--dissolve N] [--from SEC --to SEC]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import stagemix as sm  # noqa: E402

IDENT = (1.0, np.zeros(2))
TAIL = 26                    # frames the last shot runs on after the song
FADE = 22                    # frames of fade-out at the very end
W, H = sm.W, sm.H
CW, CH = W // 2, H // 2
FRAME_BYTES = W * H * 3 // 2


def cam_of(d):
    return (float(d["z"]), np.array(d["o"], float)) if d else IDENT


def lerp(c0, c1, u):
    u = u * u * (3 - 2 * u)                                    # smoothstep: no speed at either end
    return (c0[0] + (c1[0] - c0[0]) * u, c0[1] + (c1[1] - c0[1]) * u)


def warp(planes, z, o):
    """Apply x_out = z * x_src + o to a (Y, U, V) 4:2:0 frame."""
    y, u, v = planes
    my = np.array([[z, 0, o[0]], [0, z, o[1]]], np.float32)
    # chroma samples sit at luma x = 2u, y = 2v + 0.5
    mc = np.array([[z, 0, o[0] / 2], [0, z, (o[1] + 0.5 * (z - 1)) / 2]], np.float32)
    kw = dict(flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return (cv2.warpAffine(y, my, (W, H), **kw), cv2.warpAffine(u, mc, (CW, CH), **kw),
            cv2.warpAffine(v, mc, (CW, CH), **kw))


def blend(a, b, alpha):
    return tuple(cv2.addWeighted(p, 1 - alpha, q, alpha, 0) for p, q in zip(a, b))


def dim(planes, k):
    """Fade towards black: luma to 16, colour to neutral."""
    y, u, v = planes
    return (cv2.convertScaleAbs(y, alpha=k, beta=16 * (1 - k)), cv2.convertScaleAbs(u, alpha=k, beta=128 * (1 - k)),
            cv2.convertScaleAbs(v, alpha=k, beta=128 * (1 - k)))


class Shot:
    def __init__(self, sh, nxt, cuts, conf_dir, force_dissolve):
        self.src, self.start, self.end = sh["src"], sh["start"], sh["end"]
        self.matched_in = sh["kind"] == "match"
        self.matched_out = bool(nxt) and nxt["kind"] == "match"
        self.cam_in = cam_of(sh.get("cam_in"))
        self.cam_out = cam_of(nxt.get("cam_out")) if self.matched_out else IDENT
        # frames of cross-dissolve at the change into this shot / out of it
        self.d_in = (force_dissolve or sh.get("dissolve", 4)) if self.matched_in else 0
        self.d_out = (force_dissolve or nxt.get("dissolve", 4)) if self.matched_out else 0
        self.first = self.start - self.d_in // 2                            # frames needed for dissolves
        self.last = self.end + self.d_out // 2
        self.cuts = [c for c in cuts if self.start < c < self.end]
        self.path = conf_dir / f"{self.src}.mp4"
        self.proc, self.pos = None, None

    def camera(self, f):
        f = min(max(f, self.start), self.end)
        if not self.cuts:
            return lerp(self.cam_in, self.cam_out, (f - self.start) / max(1, self.end - self.start))
        if f < self.cuts[0]:
            return self.cam_in
        if f >= self.cuts[-1]:
            return self.cam_out
        return IDENT

    def read(self, f):
        if self.proc is None:
            self.proc = subprocess.Popen(
                ["ffmpeg", "-v", "error", "-ss", f"{(self.first - 0.5) / sm.FPS:.5f}", "-i", str(self.path),
                 "-frames:v", str(self.last - self.first), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                stdout=subprocess.PIPE)
            self.pos = self.first
        assert f == self.pos, (self.src, f, self.pos)
        buf = np.frombuffer(self.proc.stdout.read(FRAME_BYTES), np.uint8)
        self.pos += 1
        if self.pos >= self.last:
            self.proc.stdout.close()
            self.proc.wait()
        planes = (buf[:W * H].reshape(H, W), buf[W * H:W * H + CW * CH].reshape(CH, CW),
                  buf[W * H + CW * CH:].reshape(CH, CW))
        z, o = self.camera(f)
        if abs(z - 1) < 1e-4 and np.abs(o).max() < 0.05:
            return planes
        return warp(planes, z, o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("edl")
    ap.add_argument("audio")
    ap.add_argument("out")
    ap.add_argument("--from", dest="t0", type=float, default=None)
    ap.add_argument("--to", dest="t1", type=float, default=None)
    ap.add_argument("--dissolve", type=int, default=0,
                    help="force this many dissolve frames (even) at every matched change")
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--preset", default="slow")
    a = ap.parse_args()

    work = Path(a.work)
    edl = json.loads(Path(a.edl).read_text())
    cuts = edl["cuts"]
    sh = edl["shots"]
    shots = [Shot(s, sh[i + 1] if i + 1 < len(sh) else None, cuts[s["src"]], work / "conf", a.dissolve)
             for i, s in enumerate(sh)]
    f0 = edl["f0"] if a.t0 is None else edl["pre"] + int(round(a.t0 * sm.FPS))
    f1 = edl["f1"] if a.t1 is None else edl["pre"] + int(round(a.t1 * sm.FPS))
    if a.t1 is None:                                     # hold the final pose a moment after the music ends
        f1 = min(f1 + TAIL, edl["n_frames"][sh[-1]["src"]])
        shots[-1].end = shots[-1].last = f1
    # a partial render must not start inside a dissolve
    for s in shots:
        s.first, s.last = max(s.first, f0), min(s.last, f1)

    t_audio = (f0 - edl["pre"]) / sm.FPS
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}",
         "-r", "30000/1001", "-color_range", "tv", "-colorspace", "bt709", "-i", "-",
         "-ss", f"{t_audio:.5f}", "-i", a.audio,
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", a.preset, "-crf", str(a.crf),
         "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
         "-color_trc", "bt709", "-af", "apad", "-c:a", "aac", "-b:a", "256k", "-shortest",
         "-movflags", "+faststart", a.out],
        stdin=subprocess.PIPE)
    fade = FADE if a.t1 is None else 0
    k = 0
    for f in range(f0, f1):
        while f >= shots[k].end and k + 1 < len(shots):
            k += 1
        cur = shots[k]
        img = cur.read(f)
        prv = shots[k - 1] if k > 0 else None
        nxt = shots[k + 1] if k + 1 < len(shots) else None
        if cur.matched_in and prv is not None and f < prv.last and f >= prv.first:
            alpha = (f - (cur.start - cur.d_in // 2) + 1) / (cur.d_in + 1)   # weight of the incoming shot
            img = blend(prv.read(f), img, alpha)
        elif cur.matched_out and nxt is not None and f >= nxt.first:
            alpha = (f - (nxt.start - nxt.d_in // 2) + 1) / (nxt.d_in + 1)
            img = blend(img, nxt.read(f), alpha)
        if fade and f >= f1 - fade:
            img = dim(img, (f1 - f) / fade)
        for p in img:
            enc.stdin.write(np.ascontiguousarray(p).tobytes())
        if (f - f0) % 600 == 0:
            print(f"  {f - f0}/{f1 - f0} frames", flush=True)
    enc.stdin.close()
    enc.wait()
    print("wrote", a.out)


if __name__ == "__main__":
    main()
