"""Shared pieces of the outfit-change stage-mix pipeline.

Coordinates are pixels of the conformed 1920x1080 frames.  "Frame f" is the same
moment of the song in every stage (see conform.py).
"""
import json
import sys
from pathlib import Path

import numpy as np

FPS = 30000 / 1001
W, H = 1920, 1080
PRE = 30                     # conformed frame index of song time 0


class Source:
    """All faces of one stage video, linked into per-shot tracks."""

    def __init__(self, name, faces_npz, cuts):
        d = np.load(faces_npz)
        self.name = name
        self.n = int(d["n_frames"])
        self.frame = d["frame"]
        self.box = d["box"]
        self.kps = d["kps"].copy()
        self.emb = d["emb"].astype(np.float32)
        order = np.argsort(self.frame, kind="stable")
        assert (order == np.arange(len(order))).all()
        self.start = np.searchsorted(self.frame, np.arange(self.n + 1))   # detections of frame f: start[f]:start[f+1]
        self.size = self.box[:, 3] - self.box[:, 1]
        self.cuts = np.zeros(self.n + 1, bool)
        self.cuts[[c for c in cuts if 0 < c <= self.n and not self._faces_persist(c)]] = True
        self._link_tracks()
        self._smooth()
        self.center = self.kps.mean(1)
        eye = self.kps[:, 1] - self.kps[:, 0]
        self.roll = np.degrees(np.arctan2(eye[:, 1], eye[:, 0]))

    def dets(self, f):
        return range(self.start[f], self.start[f + 1]) if 0 <= f < self.n else range(0)

    def _faces_persist(self, c):
        """True when the faces stay where they were across a detected "cut" at frame c.

        Strobe lighting makes the picture jump like a cut does, but the performers do not move.
        """
        old, cur = list(self.dets(c - 1)), list(self.dets(c))
        if not old or not cur:
            return False
        stay = 0
        for j in old:
            for i in cur:
                iou = _iou(self.box[i], self.box[j])
                if iou < 0.5:
                    continue
                ea, eb = self.emb[i], self.emb[j]
                same = float(ea @ eb) >= 0.4 if not (np.isnan(ea[0]) or np.isnan(eb[0])) else iou >= 0.7
                if same:
                    stay += 1
                    break
        return stay >= max(1, 0.5 * min(len(old), len(cur)))

    def _link_tracks(self):
        """Greedy IoU linking between consecutive frames; a camera cut ends every track."""
        self.track = np.full(len(self.frame), -1, np.int32)
        self.prev = np.full(len(self.frame), -1, np.int32)        # same face one frame earlier
        self.next = np.full(len(self.frame), -1, np.int32)
        nxt = 0
        for f in range(self.n):
            cur = list(self.dets(f))
            old = list(self.dets(f - 1)) if f > 0 and not self.cuts[f] else []
            pairs = []
            for i in cur:
                for j in old:
                    iou = _iou(self.box[i], self.box[j])
                    if iou >= 0.3:
                        pairs.append((iou, i, j))
            used_i, used_j = set(), set()
            for iou, i, j in sorted(pairs, reverse=True):
                if i in used_i or j in used_j:
                    continue
                used_i.add(i)
                used_j.add(j)
                self.track[i] = self.track[j]
                self.prev[i], self.next[j] = j, i
            for i in cur:
                if self.track[i] < 0:
                    self.track[i] = nxt
                    nxt += 1
        self.n_tracks = nxt
        # one identity vector per track: mean of its (unit) embeddings
        self.track_emb = np.full((nxt, 512), np.nan, np.float32)
        has = ~np.isnan(self.emb[:, 0])
        for t in np.unique(self.track[has]):
            m = self.emb[has & (self.track == t)].mean(0)
            self.track_emb[t] = m / (np.linalg.norm(m) + 1e-9)
        self.track_len = np.bincount(self.track, minlength=nxt)

    def _smooth(self):
        """Light temporal smoothing of the landmarks along each track (kills detector jitter)."""
        k = self.kps.copy()
        both = (self.prev >= 0) & (self.next >= 0)
        i = np.where(both)[0]
        self.kps[i] = 0.25 * k[self.prev[i]] + 0.5 * k[i] + 0.25 * k[self.next[i]]

    def follow(self, det, steps):
        """Detection index of the same face `steps` frames later (negative = earlier), or -1."""
        link = self.next if steps > 0 else self.prev
        for _ in range(abs(steps)):
            if det < 0:
                return -1
            det = link[det]
        return det

    def track_span(self, det):
        """How many frames the face's track continues backwards and forwards from this detection."""
        b = f = 0
        d = det
        while self.prev[d] >= 0:
            d = self.prev[d]
            b += 1
        d = det
        while self.next[d] >= 0:
            d = self.next[d]
            f += 1
        return b, f


def _iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter + 1e-9)


def load_sources(work):
    work = Path(work)
    shots = json.loads((work / "shots.json").read_text())
    names = sorted(shots)
    for n in names:                  # a stage conformed again has new frame numbers: its old analysis is wrong
        conf = work / "conf" / f"{n}.mp4"
        for p, tool in ((work / "faces" / f"{n}.npz", f"faces.py for {n}"), (work / "shots.json", "shots.py")):
            if conf.exists() and p.stat().st_mtime < conf.stat().st_mtime:
                sys.exit(f"{p} is older than {conf}: run {tool} again (did it fail? see its log)")
    return [Source(n, work / "faces" / f"{n}.npz", shots[n]["cuts"]) for n in names]


# ----------------------------------------------------------------------------------------------
# aligning two stages at one frame

MAX_SCALE_RATIO = 1.9        # how much bigger one stage's faces may be than the other's
MIN_FACE = 26                # px - smaller detections are ignored when matching


def fit_scale_shift(pb, pa, w):
    """Least-squares p_a ~= s * p_b + t (no rotation) over weighted points."""
    w = w / w.sum()
    mb, ma = (pb * w[:, None]).sum(0), (pa * w[:, None]).sum(0)
    db, da = pb - mb, pa - ma
    s = float((w[:, None] * db * da).sum() / ((w[:, None] * db * db).sum() + 1e-9))
    return s, ma - s * mb


def match_frames(A, B, f):
    """Best alignment of stage B onto stage A at frame f.

    Returns None or a dict with the map  p_A = s * p_B + t  and how well the faces agree.
    """
    ia = [i for i in A.dets(f) if A.size[i] >= MIN_FACE]
    ib = [j for j in B.dets(f) if B.size[j] >= MIN_FACE]
    if not ia or not ib:
        return None
    ia.sort(key=lambda i: -A.size[i])
    ib.sort(key=lambda j: -B.size[j])
    best = None
    for i0 in ia[:3]:
        for j0 in ib[:3]:
            s0 = A.size[i0] / B.size[j0]
            if not 1 / MAX_SCALE_RATIO <= s0 <= MAX_SCALE_RATIO:
                continue
            t0 = A.center[i0] - s0 * B.center[j0]
            pairs = _pair_up(A, B, ia, ib, s0, t0)
            if not pairs:
                continue
            pa = np.concatenate([A.kps[i] for i, _ in pairs])
            pb = np.concatenate([B.kps[j] for _, j in pairs])
            w = np.concatenate([np.full(5, A.size[i]) for i, _ in pairs])
            s, t = fit_scale_shift(pb, pa, w)
            pairs = _pair_up(A, B, ia, ib, s, t)
            if not pairs:
                continue
            weight = sum(A.size[i] ** 2 for i, _ in pairs)
            if best is None or weight > best[0]:
                best = (weight, s, t, pairs)
    if best is None:
        return None
    _, s, t, pairs = best
    # residual of every matched face, in units of its own size
    res = [float(np.linalg.norm(s * B.kps[j] + t - A.kps[i], axis=1).mean() / A.size[i]) for i, j in pairs]
    wts = np.array([A.size[i] ** 2 for i, _ in pairs])
    i_main, j_main = pairs[0]
    sims = []
    for i, j in pairs:
        ea, eb = A.track_emb[A.track[i]], B.track_emb[B.track[j]]
        sims.append(float(ea @ eb) if not (np.isnan(ea[0]) or np.isnan(eb[0])) else np.nan)
    # faces that have no partner and are big enough to be noticed
    main_sz = A.size[i_main]
    ma, mb = {i for i, _ in pairs}, {j for _, j in pairs}
    lonely = sum(1 for i in ia if i not in ma and A.size[i] >= 0.6 * main_sz) + \
        sum(1 for j in ib if j not in mb and s * B.size[j] >= 0.6 * main_sz)
    roll = float((A.roll[i_main] - B.roll[j_main] + 180) % 360 - 180)
    return {"s": float(s), "t": t.astype(float), "pairs": pairs, "res": float(np.average(res, weights=wts)),
            "res_main": res[0], "sim_main": sims[0], "sims": sims, "lonely": int(lonely),
            "roll": roll, "main_size": float(main_sz), "n": len(pairs)}


def _pair_up(A, B, ia, ib, s, t):
    """One-to-one pairing of B faces (mapped by s, t) with A faces lying at the same place and size."""
    cand = []
    for i in ia:
        for j in ib:
            ratio = s * B.size[j] / A.size[i]
            if not 0.72 <= ratio <= 1.38:
                continue
            dist = np.linalg.norm(s * B.center[j] + t - A.center[i]) / A.size[i]
            if dist <= 0.42:
                cand.append((dist, i, j))
    used_i, used_j, pairs = set(), set(), []
    for dist, i, j in sorted(cand):
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        pairs.append((i, j))
    pairs.sort(key=lambda p: -A.size[p[0]])
    return pairs


# ----------------------------------------------------------------------------------------------
# virtual camera: every stage is shown through  x_out = z * x_src + o  with the frame fully covered

def cover_range(z, extent):
    """Offsets o that keep [0, extent] covered at zoom z."""
    return -(z - 1) * extent, 0.0


def solve_cameras(s, t, max_zoom):
    """Smallest zooms (zA, oA), (zB, oB) with  camB = camA o M  where M: p_A = s p_B + t.

    Returns None when even max_zoom cannot bring the two pictures into register
    without showing a border.
    """
    zA = max(1.0, 1.0 / s)
    while max(zA, zA * s) <= max_zoom + 1e-9:
        zB = zA * s
        oA, ok = np.zeros(2), True
        for k, ext in enumerate((W, H)):
            lo_a, hi_a = cover_range(zA, ext)
            lo_b, hi_b = cover_range(zB, ext)
            lo, hi = max(lo_a, lo_b - zA * t[k]), min(hi_a, hi_b - zA * t[k])   # oB = oA + zA * t
            if lo > hi:
                ok = False
                break
            # prefer both pictures zoomed about their centre
            want = 0.5 * (-(zA - 1) * ext / 2 + (-(zB - 1) * ext / 2 - zA * t[k]))
            oA[k] = min(max(want, lo), hi)
        if ok:
            return (zA, oA), (zB, oA + zA * t)
        zA += 0.01
    return None
