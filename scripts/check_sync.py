"""Independent check of the picture sync between conformed stages.

Whenever two stages show the same member's face at the same time, her head moves
through the same choreography in both, so the two face trajectories must line up
in time.  The lag that best aligns their velocities, pooled over the whole song,
is the remaining picture offset between the two stages (ideally ~0 frames).

With --apply the measured offsets are written back into WORK_DIR/sync.json as picture delay
("vdelay"); the stages it lists must then be conformed and analysed again.

usage: check_sync.py WORK_DIR [--apply]
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import stagemix as sm  # noqa: E402

HALF = 10          # half window in frames
MAXLAG = 5


def track_series(S, det, half):
    """Face centre (in face sizes) for frames -half..half around a detection, or None."""
    out = np.zeros((2 * half + 1, 2))
    for k in range(-half, half + 1):
        d = S.follow(det, k)
        if d < 0:
            return None
        out[k + half] = S.center[d] / S.size[det]
    return out


def apply(work, late):
    """Correct sync.json by the measured lateness (frames) of each stage.

    conform.py can only show whole source frames, so a stage's file is off from its ideal shift
    by a rounding error.  Only differences between stages matter, which leaves one common shift
    free: it is chosen to make the worst rounding error as small as possible.
    """
    path = work / "sync.json"
    sync = json.loads(path.read_text())
    fps = json.loads((work / "lowres" / "fps.json").read_text())

    def per(n):                  # source frames per output frame: 1 (30 fps source) or 2 (60 fps)
        return round(fps[n] / sm.FPS)

    def used(n, shift):          # the shift conform.py really produces (measured): a 30 fps source lands on the
        m = per(n)               # nearest frame, a 60 fps source on the half frame below
        return np.floor(shift * m) / m if m > 1 else np.round(shift)

    cur = {n: sm.PRE - (sync["clips"][n]["offset"] + sync["clips"][n].get("vdelay", 0.0)) * sm.FPS for n in late}
    ideal = {n: used(n, cur[n]) - late[n] for n in late}
    common = min(np.arange(-0.5, 0.5, 0.01), key=lambda c: max(abs(used(n, ideal[n] + c) - ideal[n] - c) for n in late))
    redo = []
    for n in late:
        new = ideal[n] + common
        # store the middle of the step the file lands on, so rounding the number cannot move the file
        stored = used(n, new) + (0.25 if per(n) > 1 else 0.0)
        vdelay = round((sm.PRE - stored) / sm.FPS - sync["clips"][n]["offset"], 4)
        if used(n, new) != used(n, cur[n]):
            redo.append(n)
        print(f"  {n:18s} vdelay {sync['clips'][n].get('vdelay', 0.0):+.4f} -> {vdelay:+.4f} s   "
              f"will remain {used(n, new) - new:+.2f} frames   " + ("RE-CONFORM" if n in redo else "file stays as it is"))
        sync["clips"][n]["vdelay"] = vdelay
    if not redo:
        print("no stage moves by a frame: what remains is rounding, the sync is as good as whole frames allow "
              "(sync.json left as it was)")
        return
    path.write_text(json.dumps(sync, indent=1))
    print("sync.json updated.  Conform these stages again, then faces.py for them, shots.py, and check_sync.py:")
    print('REDO="' + " ".join(redo) + '"')


def main():
    src = sm.load_sources(sys.argv[1])
    n = len(src)
    rows, lags, wts = [], [], []
    for a in range(n):
        for b in range(a + 1, n):
            A, B = src[a], src[b]
            cc = np.zeros(2 * MAXLAG + 1)
            used = 0
            for f in range(sm.PRE + 20, min(A.n, B.n) - 20, 6):
                m = sm.match_frames(A, B, f)
                if m is None or not m["sim_main"] == m["sim_main"] or m["sim_main"] < 0.5:
                    continue
                i, j = m["pairs"][0]
                xa, xb = track_series(A, i, HALF + MAXLAG), track_series(B, j, HALF + MAXLAG)
                if xa is None or xb is None:
                    continue
                va, vb = np.diff(xa, axis=0), np.diff(xb, axis=0)
                va, vb = va - va.mean(0), vb - vb.mean(0)
                if np.abs(va).mean() < 0.004 or np.abs(vb).mean() < 0.004:
                    continue                                   # she is standing still: no timing information
                core = slice(MAXLAG, MAXLAG + 2 * HALF)
                for li, lag in enumerate(range(-MAXLAG, MAXLAG + 1)):
                    sh = slice(MAXLAG + lag, MAXLAG + lag + 2 * HALF)
                    cc[li] += float((va[sh] * vb[core]).sum() /
                                    (np.linalg.norm(va[sh]) * np.linalg.norm(vb[core]) + 1e-9))
                used += 1
            if used < 15:
                continue
            k = int(np.argmax(cc))
            frac = 0.0
            if 0 < k < len(cc) - 1:
                den = cc[k - 1] - 2 * cc[k] + cc[k + 1]
                frac = 0.5 * (cc[k - 1] - cc[k + 1]) / den if den else 0.0
            lag = k - MAXLAG + frac                           # A must be read `lag` frames later to match B
            r = np.zeros(n)
            r[a], r[b] = 1, -1
            rows.append(r)
            lags.append(lag)
            wts.append(np.sqrt(used))
            print(f"{A.name[:14]:15s} vs {B.name[:14]:15s} windows {used:4d}  lag {lag:+.2f} frames  peak {cc[k] / used:.2f}")
    if not rows:
        sys.exit("no pair of stages shows the same face in motion often enough to measure anything")
    rows.append(np.ones(n))
    lags.append(0)
    wts.append(10)
    Wm = np.array(wts)[:, None]
    d = np.linalg.lstsq(np.array(rows) * Wm, np.array(lags) * Wm[:, 0], rcond=None)[0]
    res = (np.array(rows[:-1]) @ d - np.array(lags[:-1]))
    print("\nremaining picture offset per stage (frames; + = this stage's picture is LATE):")
    for s, v in zip(src, d):
        print(f"  {s.name:18s} {v:+.2f}  ({v / sm.FPS * 1000:+.0f} ms)")
    print(f"fit residual rms {np.sqrt((res ** 2).mean()):.2f} frames")
    measured = np.abs(np.array(rows[:-1])).sum(0) > 0
    for s, ok in zip(src, measured):
        if not ok:
            print(f"  {s.name}: NOT MEASURED - too few moments where it shows the same face as another stage")
    if "--apply" in sys.argv:
        apply(Path(sys.argv[1]), {s.name: float(v) for s, v, ok in zip(src, d, measured) if ok})


if __name__ == "__main__":
    main()
