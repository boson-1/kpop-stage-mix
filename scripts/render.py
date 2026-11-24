"""Render the outfit-change mix described by an EDL.

Every shot is seen through a "virtual camera" (zoom + shift, never showing a
border).  At a face-matched change the outgoing and incoming cameras are the pair
solved in plan.py, so the member's face sits at the same place and size in both
pictures while one replaces the other.  Between changes the camera eases back, or
snaps back invisibly on the broadcast director's own camera cut.

A change is a short cross-dissolve, or the mask transition plan.py gave it ("fx" in the EDL).
A mask transition shows one picture on each side of an edge instead of mixing them everywhere.
  bloom  the new stage shows on the matched face alone, holds there, then spreads over the
         frame behind a ragged, lit edge
  iris   the textbook wipe: a grayscale card says in which order the pixels change (0 first ..
         1 last) and a soft edge travels through that order.  Here the card is the distance
         from the matched face: the new stage opens out of the face in a growing circle
  sweep  the same wipe with a ramp along "dir" as the card: a straight soft edge crosses the frame

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
YS, XS = np.mgrid[0:H, 0:W].astype(np.float32)


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


def order_map(fx):
    """The card of an iris or a sweep: when each pixel changes, 0 first .. 1 last."""
    if fx["type"] == "iris":
        g = np.hypot(XS - fx["c"][0], YS - fx["c"][1])
    else:
        g = XS * fx["dir"][0] + YS * fx["dir"][1]
    g -= g.min()
    return g / g.max()


def mix(a, b, m):
    """a where the mask m is 0, b where it is 1."""
    mc = cv2.resize(m, (CW, CH), interpolation=cv2.INTER_AREA)
    return tuple(cv2.blendLinear(x, y, 1 - w, w) for x, y, w in zip(a, b, (m, mc, mc)))


def matte(a, b, order, p, soft):
    """a where the soft edge has not arrived yet, b where it has passed; p is the progress 0..1."""
    m = np.clip((p * (1 + soft) - order) / soft, 0, 1)
    return mix(a, b, m * m * (3 - 2 * m))


def bloom(a, b, into, f):
    """Frame f of a bloom: picture b shows on the matched face alone, holds there, then spreads.

    The opening is an ellipse on the face of the incoming shot and follows that face.  The face
    changes like a 2-frame dissolve, the opening waits "hold" frames, then it runs out to the
    corners behind an edge that noise makes ragged and that is lit while it travels.
    """
    t, k = into.t_in, f - into.start
    z, o = into.camera(f)
    cx, cy = (float(v) for v in z * np.array(t["face"][k + t["pre"]]) + o)
    r = 0.36 * z * t["size"]                                       # half width of the opening; it is 1.3 times as high
    g = np.maximum(np.hypot((XS - cx) / r, (YS - cy) / (1.3 * r)) - 1, 0) * r         # px outside the opening
    rng = np.random.default_rng(into.start)                        # the same ragged shape on every frame
    n = sum(w * cv2.resize(rng.random((9 * s, 16 * s), np.float32), (W, H), interpolation=cv2.INTER_CUBIC)
            for s, w in ((1, 4 / 7), (2, 2 / 7), (4, 1 / 7)))
    g = np.maximum(g + (2 * n - 1) * np.minimum(g, 0.9 * r + 0.45 * g), 0)            # none on the face, more far out
    hold, u = t["hold"], 0.0
    x = 0.08 * r * min(max(k, 0), hold)                            # px beyond the opening that have changed
    if k > hold:
        u = (k - hold) / (t["post"] - hold)                        # share of the way out, slow at first
        x += (float(g.max()) - x) * u ** 2.2
    soft = 0.6 * r + 0.1 * x
    m = np.clip((x + soft - g) / soft, 0, 1)
    m = m * m * (3 - 2 * m) * min(1.0, (k + t["pre"] + 1) / (t["pre"] + 2))
    y, cb, cr = mix(a, b, m)
    if not u or not t["glow"]:
        return y, cb, cr
    lit = 4 * m * (1 - m) * (4 * u * (1 - u)) ** 0.5               # on the edge only, none as it sets off and arrives
    lc = 0.6 * cv2.resize(lit, (CW, CH), interpolation=cv2.INTER_AREA)
    grey = np.full((CH, CW), 128, np.uint8)
    y = np.maximum(y, np.minimum(cv2.add(y, (t["glow"] * lit).astype(np.uint8)), 235))
    return y, cv2.blendLinear(cb, grey, 1 - lc, lc), cv2.blendLinear(cr, grey, 1 - lc, lc)


def join(a, b, into, f):
    """Frame f of the change from picture a to picture b, the picture of shot `into`."""
    t = into.t_in
    if t["type"] == "bloom":
        return bloom(a, b, into, f)
    p = (f - (into.start - t["pre"]) + 1) / (t["pre"] + t["post"] + 1)     # in a dissolve: weight of the incoming shot
    if t["type"] == "dissolve":
        return blend(a, b, p)
    return matte(a, b, order_map(t), p, t["soft"])


def transition(sh, force_dissolve):
    """How the change into shot `sh` is made, and how many frames before / after its start it needs."""
    if "fx" in sh and not force_dissolve:
        return sh["fx"]
    d = force_dissolve or sh.get("dissolve", 4)
    return {"type": "dissolve", "pre": d // 2, "post": d // 2}


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
        # the transition at the change into this shot / out of it
        self.t_in = transition(sh, force_dissolve) if self.matched_in else None
        self.t_out = transition(nxt, force_dissolve) if self.matched_out else None
        self.first = self.start - (self.t_in["pre"] if self.t_in else 0)   # frames needed for transitions
        self.last = self.end + (self.t_out["post"] if self.t_out else 0)
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
                    help="force a dissolve of this many frames (even) at every matched change, "
                         "also where the plan asks for a mask transition")
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
            img = join(prv.read(f), img, cur, f)
        elif cur.matched_out and nxt is not None and f >= nxt.first:
            img = join(img, nxt.read(f), nxt, f)
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
