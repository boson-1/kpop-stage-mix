"""Choose where to change outfit.

1. For every pair of stages and every frame, test whether the two pictures can be
   brought into register on the same member's face (stagemix.match_frames) and score it.
2. Pick the edit path with dynamic programming: reward good face matches (more on
   the beat), allow plain switches on the director's own camera cuts, keep shots
   between ~1 and ~7 seconds, never bounce straight back to the stage just left,
   and spread screen time over all stages.
3. Give face-matched changes that land on a strong beat a mask transition (mask_fx):
   a bloom or an iris out of the face, or a sweep.  The others keep the short cross-dissolve.

usage: plan.py WORK_DIR OUT_EDL.json [START_SEC]
START_SEC: song time the mix opens at (default 0) - skips a silent lead-in of the reference track.
"""
import json
import sys
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).parent))
import stagemix as sm  # noqa: E402

MAX_ZOOM = 1.6
WIN = 3                      # frames either side of a change that must be cut-free and tracked
MIN_SHOT = 28                # frames (~2 beats)
MAX_SHOT = 210               # frames (7 s)
MIN_SEG = 12                 # frames a picture must stay up next to a change (no flash frames)
SIM_SAME = 0.42              # identity similarity above which two faces are the same member
PER_SWITCH = 0.50            # what a change must be worth to happen at all
FX_EVERY = 2                 # mask transitions on changes that hit every n-th beat (4: bar starts only, 1: any beat, 0: none)
FX_BLOOM = 8                 # bar starts on every n-th beat get a bloom, the other bar starts an iris (4: bloom on all, 0: on none)
FX_GAP = 30                  # frames without a mask transition between two of them
FX_LEN = 10                  # frames an iris or a sweep takes, and a bloom after the frame of the change
FX_SOFT = 0.15               # width of the soft edge of an iris or a sweep, as a share of the way the edge travels
FX_HOLD = 3                  # frames of FX_LEN a bloom shows the new face alone before it spreads
FX_GLOW = 90                 # brightness added on the spreading edge of a bloom, out of 255 (0: none)

SRC = None                   # list[Source], filled before the worker pool forks


def quality(m, drift, zoom):
    """0..1 - how invisible the join will be."""
    q = np.exp(-(m["res"] / 0.10) ** 2) * np.exp(-(drift / 0.22) ** 2)
    q *= np.exp(-((zoom - 1) / 0.55) ** 2) * np.exp(-(m["roll"] / 9.0) ** 2)
    q *= 0.75 ** m["lonely"]
    return float(q)


def pair_candidates(ab):
    a, b = ab
    A, B = SRC[a], SRC[b]
    rows = []
    n = min(A.n, B.n)
    cutA = np.convolve(A.cuts[:n].astype(int), np.ones(2 * WIN + 1, int), mode="same") > 0
    cutB = np.convolve(B.cuts[:n].astype(int), np.ones(2 * WIN + 1, int), mode="same") > 0
    for f in range(sm.PRE + WIN, n - WIN - 1):
        if cutA[f] or cutB[f]:
            continue
        m = sm.match_frames(A, B, f)
        if m is None or m["res"] > 0.14 or abs(m["roll"]) > 14:
            continue
        i, j = m["pairs"][0]
        # identity: the main pair must be the same member; tiny faces need a whole formation instead
        if np.isnan(m["sim_main"]):
            if m["n"] < 4:
                continue
        elif m["sim_main"] < SIM_SAME:
            continue
        if any(s == s and s < 0.2 for s in m["sims"]):
            continue                                     # some other matched pair is clearly two different members
        if m["n"] <= 2 and m["main_size"] < 60:
            continue
        drift = 0.0
        for k in (-WIN, WIN):
            ik, jk = A.follow(i, k), B.follow(j, k)
            if ik < 0 or jk < 0:
                drift = 9.0
                break
            d = np.linalg.norm(m["s"] * B.center[jk] + m["t"] - A.center[ik]) / A.size[i]
            drift = max(drift, float(d))
        if drift > 0.45:
            continue
        cams = sm.solve_cameras(m["s"], m["t"], MAX_ZOOM)
        if cams is None:
            continue
        (zA, oA), (zB, oB) = cams
        q = quality(m, drift, max(zA, zB))
        if q < 0.25:
            continue
        # how long has / will the matched face been on screen in each stage (guards against a
        # broadcast dissolve or an undetected cut right next to the change)
        backA, fwdA = A.track_span(i)
        backB, fwdB = B.track_span(j)
        rows.append((f, a, b, q, zA, oA[0], oA[1], zB, oB[0], oB[1], m["res"], drift, m["roll"],
                     m["sim_main"], m["n"], m["main_size"] * zA, m["lonely"], backA, fwdA, backB, fwdB))
    return rows


def beat_bonus(frames, beats):
    """Extra reward for a change that lands on a beat (more on bar and 2-bar starts)."""
    t = (np.asarray(frames) - sm.PRE) / sm.FPS
    k = np.round((t - beats["phase"]) / beats["period"])
    d = np.abs(t - (beats["phase"] + k * beats["period"])) * sm.FPS        # frames away from the beat
    near = np.clip(1 - d / 1.6, 0, 1)
    w = np.where(k % 8 == 0, 0.34, np.where(k % 4 == 0, 0.28, np.where(k % 2 == 0, 0.20, 0.14)))
    return near * w


def len_score(L):
    L = np.asarray(L, float)
    s = np.where(L < 45, -0.25 * (45 - L) / 17, 0.0)                      # a bit short
    return s - 0.35 * (np.maximum(L - 120, 0) / 30) ** 1.5                # dragging on


def mask_fx(r, k, into):
    """Mask transition for the face-matched change of candidate row r, on beat k, into stage `into`.

    Several faces matched: a sweep across the frame, the way the faces are travelling.
    One face: on a bar start a bloom out of it (every FX_BLOOM-th beat) or an iris opening out of
    it, on other beats a sweep down the body.
    Either way the matched face changes on the frame of the change: the one moment at which the
    two pictures are known to be in register there.  An iris or a sweep times its soft edge to pass
    the face then.  A bloom changes the face first and follows the new face, which must stay
    tracked; where it does not, the change gets the iris.
    """
    A, B = SRC[int(r[1])], SRC[int(r[2])]
    i, j = sm.match_frames(A, B, int(r[0]))["pairs"][0]
    c = r[4] * A.center[i] + r[5:7]                      # the matched face on screen, the same spot in both pictures
    if r[14] > 1:
        right = bool(A.center[A.follow(i, WIN)][0] >= A.center[A.follow(i, -WIN)][0])
        fx, at = {"type": "sweep", "dir": [1, 0] if right else [-1, 0]}, c[0] / sm.W if right else 1 - c[0] / sm.W
    elif k % 4 == 0:
        S, d = (A, i) if into == int(r[1]) else (B, j)
        dets = [S.follow(d, n) for n in range(-1, FX_LEN)]           # the new face, from the frame before the change
        if FX_BLOOM and k % FX_BLOOM == 0 and min(dets) >= 0:
            return {"type": "bloom", "pre": 1, "post": FX_LEN, "hold": FX_HOLD, "glow": FX_GLOW,
                    "size": round(float(S.size[d]), 1),              # height of the new face, in its own stage
                    "face": [[round(float(v), 1) for v in S.center[n]] for n in dets]}
        fx, at = {"type": "iris", "c": [round(float(c[0]), 1), round(float(c[1]), 1)]}, 0.0
    else:
        fx, at = {"type": "sweep", "dir": [0, 1]}, c[1] / sm.H
    # frames before the change at which the edge sets off, so that it is half way over the face at the change
    pre = max(0, int(round((at + FX_SOFT / 2) / (1 + FX_SOFT) * (FX_LEN + 1) - 1)))
    fx.update(soft=FX_SOFT, pre=pre, post=FX_LEN - pre)
    return fx


class NoPath(Exception):
    """No sequence of changes reaches the end of the song; the argument is the last frame a shot can start at."""


def run_dp(edges, n_src, f0, f1, rent, can_start):
    """Best path through the change edges.  Returns [(shot_start_frame, stage, edge_index)]."""
    NEG = -1e9
    nF = f1 + 1
    # best[f][s]: best score of a shot of stage s starting at f; the runner-up from a different
    # previous stage is kept too, so the next step can forbid going straight back
    best = np.full((nF, n_src), NEG)
    bestp = np.full((nF, n_src), -1, int)
    beste = np.full((nF, n_src), -1, int)
    second = np.full((nF, n_src), NEG)
    seconde = np.full((nF, n_src), -1, int)
    best[f0] = np.where(can_start, 0.0, NEG)
    back = {}
    for ei in np.argsort(edges["f"], kind="stable"):
        e = edges[ei]
        f, a, b = int(e["f"]), int(e["a"]), int(e["b"])
        lo, hi = max(f0, f - MAX_SHOT), f - MIN_SHOT
        if hi < lo:
            continue
        fs = np.arange(lo, hi + 1)
        L = f - fs
        via_best = bestp[fs, a] != b
        v = np.where(via_best, best[fs, a], second[fs, a]) + len_score(L) - rent[a] * L
        k = int(np.argmax(v))
        val = v[k] + e["gain"]
        if val < NEG / 2:
            continue
        back[int(ei)] = (int(fs[k]), int(beste[fs[k], a] if via_best[k] else seconde[fs[k], a]))
        if val > best[f, b]:
            if bestp[f, b] != a:
                second[f, b], seconde[f, b] = best[f, b], beste[f, b]
            best[f, b], bestp[f, b], beste[f, b] = val, a, ei
        elif bestp[f, b] != a and val > second[f, b]:
            second[f, b], seconde[f, b] = val, ei
    end_best, end_arg = NEG, None
    for s in range(n_src):
        fs = np.arange(max(f0, f1 - MAX_SHOT), f1 - MIN_SHOT + 1)
        L = f1 - fs
        v = best[fs, s] + len_score(L) - rent[s] * L
        k = int(np.argmax(v))
        if v[k] > end_best:
            end_best, end_arg = v[k], (int(fs[k]), s)
    if end_arg is None:
        raise NoPath(int(np.where((best > NEG / 2).any(1))[0].max()))
    f, s = end_arg
    ei = int(beste[f, s])
    path = []
    while True:
        path.append((f, s, ei))
        if ei < 0:
            break
        pf, pe = back[ei]
        f, s, ei = pf, int(edges[ei]["a"]), pe
    return path[::-1]


def main():
    global SRC
    work, out = Path(sys.argv[1]), Path(sys.argv[2])
    SRC = sm.load_sources(work)
    names = [s.name for s in SRC]
    n_src = len(SRC)
    beats = json.loads((work / "beats.json").read_text())
    sync = json.loads((work / "sync.json").read_text())
    dur = sync["ref_dur"]
    start = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    f0, f1 = sm.PRE + int(round(start * sm.FPS)), sm.PRE + int(dur * sm.FPS) - 1
    # first conformed frame holding a real picture: a clip that starts after the song did is padded (conform.py)
    real = [max(0, int(np.ceil(sm.PRE - (c["offset"] + c.get("vdelay", 0.0)) * sm.FPS)))
            for c in (sync["clips"][n] for n in names)]

    cache = work / "candidates.npy"
    newest = max(p.stat().st_mtime for p in [work / "shots.json", *(work / "faces").glob("*.npz")])
    if cache.exists() and cache.stat().st_mtime > newest:        # still describes the current faces and cuts
        cand = np.load(cache)
    else:
        pairs = [(a, b) for a in range(n_src) for b in range(a + 1, n_src)]
        with get_context("fork").Pool() as pool:           # workers inherit SRC
            rows = [r for part in pool.map(pair_candidates, pairs, chunksize=1) for r in part]
        cand = np.array(rows, float)
        np.save(cache, cand)
    print(f"{len(cand)} face-matched candidate frames over {n_src * (n_src - 1) // 2} stage pairs")

    # frames since the last / until the next camera cut, per stage
    since, until = [], []
    for s in SRC:
        c = np.where(s.cuts)[0]
        fr = np.arange(s.n + 1)
        prev = np.searchsorted(c, fr, side="right") - 1
        since.append(np.where(prev >= 0, fr - c[np.maximum(prev, 0)], 10 ** 6))
        nxt = np.searchsorted(c, fr, side="right")
        until.append(np.where(nxt < len(c), c[np.minimum(nxt, len(c) - 1)] - fr, 10 ** 6))

    def clean(a, b, f):
        """Stage a has shown its picture for MIN_SEG frames before f; stage b will for MIN_SEG after."""
        return since[a][f - 1] >= MIN_SEG - 1 and until[b][f] >= MIN_SEG and f - 2 >= real[b]

    # --- edges: both directions of every matched frame, plus plain switches on camera cuts
    dt = np.dtype([("f", int), ("a", int), ("b", int), ("gain", float), ("kind", int), ("ci", int)])
    ed = []
    bb = beat_bonus(cand[:, 0], beats)
    for ci, r in enumerate(cand):
        f, a, b, q = int(r[0]), int(r[1]), int(r[2]), r[3]
        if not f0 + MIN_SHOT <= f <= f1 - MIN_SHOT:
            continue
        g = 1.25 * q * (1.0 + 1.2 * bb[ci]) + bb[ci] - PER_SWITCH
        backA, fwdA, backB, fwdB = r[17:21]
        if backA >= MIN_SEG and fwdB >= MIN_SEG and clean(a, b, f):
            ed.append((f, a, b, g, 0, ci))
        if backB >= MIN_SEG and fwdA >= MIN_SEG and clean(b, a, f):
            ed.append((f, b, a, g, 0, ci))
    for b in range(n_src):                              # plain switch into a fresh camera shot of stage b
        cf = np.where(SRC[b].cuts[:f1])[0]
        cf = cf[(cf > f0 + MIN_SHOT) & (cf < f1 - MIN_SHOT)]
        for f, bonus in zip(cf, beat_bonus(cf, beats)):
            f = int(f)
            for a in range(n_src):
                if a != b and clean(a, b, f):
                    both = bool(SRC[a].cuts[f])
                    ed.append((f, a, b, (0.22 if both else 0.12) + 0.5 * bonus - PER_SWITCH, 1, -1))
    edges = np.array(ed, dt)
    print(f"{(edges['kind'] == 0).sum()} match edges, {(edges['kind'] == 1).sum()} camera-cut edges")

    # no camera cut right after frame 0, and the stage's picture has begun
    can_start = np.array([until[s][f0] >= MIN_SEG and real[s] <= f0 for s in range(n_src)])

    def no_path(what):
        x, sr = sf.read(work / "audio" / f"{sync['ref']}.wav", dtype="float32")
        lead = float(np.argmax(np.abs(x) > 10 ** (-45 / 20))) / sr          # first sample above -45 dB
        sys.exit(f"{what}\nOnly a stage whose own picture has begun, and that can be left within {MAX_SHOT} frames, "
                 f"can open the mix; official clips often begin a moment after the song's first sample.\n"
                 f"The reference track is silent until {lead:.2f} s, so a START_SEC up to that cuts nothing but "
                 f"silence: try START_SEC = {max(lead - 0.02, 0):.2f}.  (Pictures begin at song time: "
                 + ", ".join(f"{n[:2]} {max(r - sm.PRE, 0) / sm.FPS:.2f} s" for n, r in zip(names, real)) + ")")

    if not can_start.any():
        no_path(f"no stage can open the mix at song time {start:.2f} s.")

    # --- DP, re-run while nudging screen time towards an even split ("rent" per frame on over-used stages)
    def usage(path):
        starts = [p[0] for p in path] + [f1]
        use = np.zeros(n_src)
        for (f, s, _), nf in zip(path, starts[1:]):
            use[s] += nf - f
        return starts, use / use.sum()

    rent, keep = np.zeros(n_src), None
    for it in range(60):
        try:
            path = run_dp(edges, n_src, f0, f1, rent, can_start)
        except NoPath as e:
            no_path(f"no edit path reaches the end of the song: the last shot that can start is at song time "
                    f"{(e.args[0] - sm.PRE) / sm.FPS:.2f} s, and no face match or camera cut lets the mix leave it.")
        starts, share = usage(path)
        gain = sum(edges[p[2]]["gain"] for p in path if p[2] >= 0)       # reward without the rent
        spread = float(np.abs(share - 1 / n_src).sum())
        if keep is None or gain - 25 * spread > keep[0]:
            keep = (gain - 25 * spread, path, starts, share, it, gain, spread)
        rent += 0.004 / (1 + it / 15) * (share - 1 / n_src) * n_src
        rent -= rent.min()
    _, path, starts, share, it, gain, spread = keep
    print(f"kept iteration {it}: reward {gain:.1f}, share spread {spread:.2f}")

    kinds = [int(edges[p[2]]["kind"]) if p[2] >= 0 else -1 for p in path]
    print(f"path: {len(path)} shots, {kinds.count(0)} face-matched changes, {kinds.count(1)} camera-cut switches")
    print("screen share: " + ", ".join(f"{n[:2]}:{100 * s:.0f}%" for n, s in zip(names, share)))
    L = np.diff(starts) / sm.FPS
    print(f"shot length s: median {np.median(L):.2f}, min {L.min():.2f}, max {L.max():.2f}")

    shots, matched = [], []
    for (f, s, ei), nf in zip(path, starts[1:]):
        sh = {"src": names[s], "start": int(f), "end": int(nf), "t_song": round((f - sm.PRE) / sm.FPS, 3)}
        if ei >= 0:
            e = edges[ei]
            sh["kind"] = "match" if e["kind"] == 0 else "cut"
            if e["kind"] == 0:
                r = cand[e["ci"]]
                a_is_out = int(r[1]) == int(e["a"])
                cam_first = {"z": r[4], "o": [r[5], r[6]]}        # camera of the lower-numbered stage
                cam_second = {"z": r[7], "o": [r[8], r[9]]}
                sh["cam_out"] = cam_first if a_is_out else cam_second     # previous shot's camera at the join
                sh["cam_in"] = cam_second if a_is_out else cam_first      # this shot's camera at the join
                sh["dissolve"] = 2 if r[3] >= 0.5 else 4                  # tight matches can change faster
                sh.update(q=round(r[3], 3), res=round(r[10], 3), drift=round(r[11], 3), roll=round(r[12], 1),
                          sim=None if np.isnan(r[13]) else round(r[13], 2), n_faces=int(r[14]),
                          face_px=round(r[15]), beat=round(float(bb[e["ci"]]), 2))
                # frames the new face has been on screen before the change, and the old one stays after it
                matched.append((len(shots), r, s, *((r[19], r[18]) if a_is_out else (r[17], r[20]))))
        else:
            sh["kind"] = "start"
        shots.append(sh)

    # --- mask transitions on strong beats: bar starts choose first, then the other beats
    def span(j, fx=None):
        """Frames around its start that the change into shot j keeps both pictures up."""
        fx = fx or shots[j].get("fx") or dict.fromkeys(("pre", "post"), shots[j].get("dissolve", 0) // 2)
        return shots[j]["start"] - fx["pre"], shots[j]["start"] + fx["post"]

    for bar_start in (True, False):
        for i, r, s, back_in, fwd_out in matched:
            t = (shots[i]["start"] - sm.PRE) / sm.FPS - beats["phase"]
            k = int(round(t / beats["period"]))
            if not FX_EVERY or k % FX_EVERY or (k % 4 == 0) != bar_start or abs(t - k * beats["period"]) * sm.FPS > 0.8:
                continue
            fx = mask_fx(r, k, s)
            lo, hi = span(i, fx)
            # both faces on screen for as long as both pictures are needed, which also rules out a camera cut;
            # steady picture between this change and its neighbours; and no other mask transition close by
            if (back_in >= fx["pre"] and fwd_out >= fx["post"] and lo - 2 >= real[s]
                    and lo >= span(i - 1)[1] + MIN_SEG and hi + MIN_SEG <= (span(i + 1)[0] if i + 1 < len(shots) else f1)
                    and all(lo - span(j)[1] >= FX_GAP or span(j)[0] - hi >= FX_GAP
                            for j, x in enumerate(shots) if "fx" in x)):
                shots[i]["fx"] = fx

    cuts = {s.name: [int(c) for c in np.where(s.cuts)[0]] for s in SRC}
    n_frames = {s.name: s.n for s in SRC}
    out.write_text(json.dumps({"fps": sm.FPS, "pre": sm.PRE, "f0": f0, "f1": f1, "shots": shots,
                               "cuts": cuts, "n_frames": n_frames}, indent=1))
    qs = [s["q"] for s in shots if s["kind"] == "match"]
    print(f"match quality: median {np.median(qs):.2f}, min {min(qs):.2f}; on-beat changes: "
          f"{sum(1 for s in shots if s.get('beat', 0) > 0.07)}/{len(qs)}")
    fxs = [s["fx"]["type"] for s in shots if "fx" in s]
    print(f"mask transitions: {len(fxs)} of the {len(qs)} face-matched changes ("
          + ", ".join(f"{fxs.count(t)} {t}" for t in ("bloom", "iris", "sweep")) + "), the others dissolve")


if __name__ == "__main__":
    main()
