"""Fit a constant-tempo beat grid to the studio track and find where the bars start.

Pop tracks are produced to a click, so one period and one phase describe every beat of the
song.  The grid is the one that collects the most onset energy; the bar start is the beat
(of every 4, then of every 8) where the harmony changes most.

Writes {"period", "phase"} in seconds of the track: beat k is at phase + k * period, and
k = 0, 8, 16 ... start a two-bar phrase.  plan.py rewards changes that land on them.

usage: beats.py REF.wav OUT.json
"""
import json
import sys

import librosa
import numpy as np

SR = 22050


def grid_score(t_env, env, period, phase, lo=0.0, hi=None):
    hi = t_env[-1] if hi is None else hi
    g = np.arange(phase + np.ceil((lo - phase) / period) * period, hi, period)
    return np.interp(g, t_env, env).mean()


def fit(y, hop, periods, phases):
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=hop)
    t_env = np.arange(len(env)) * hop / SR
    _, p, ph = max((grid_score(t_env, env, p, ph), p, ph) for p in periods for ph in phases)
    return p, ph, t_env, env


def main():
    y, _ = librosa.load(sys.argv[1], sr=SR)
    tempo, _ = librosa.beat.beat_track(y=y, sr=SR, hop_length=256)
    p0 = 60 / float(np.atleast_1d(tempo)[0])
    # coarse over +-3 % of librosa's estimate, then two refinements (the last on a finer envelope)
    p, ph, _, _ = fit(y, 256, np.linspace(p0 * 0.97, p0 * 1.03, 601), np.linspace(0, p0, 60, endpoint=False))
    p, ph, _, _ = fit(y, 256, np.linspace(p - 0.0006, p + 0.0006, 241), np.linspace(ph - 0.02, ph + 0.02, 81))
    p, ph, t_env, env = fit(y, 64, np.linspace(p - 0.0003, p + 0.0003, 121), np.linspace(ph - 0.02, ph + 0.02, 81))
    ph %= p
    print(f"period {p:.6f} s = {60 / p:.2f} BPM, first beat at {ph:.4f} s")

    # a constant grid must fit every part of the song equally well
    edges = np.linspace(0, t_env[-1], 5)
    drift = []
    for lo, hi in zip(edges, edges[1:]):
        cands = np.linspace(ph - 0.06, ph + 0.06, 241)
        drift.append(1000 * (cands[int(np.argmax([grid_score(t_env, env, p, c, lo, hi) for c in cands]))] - ph))
    print("best phase per quarter of the song, ms from the fit: " + " ".join(f"{d:+.0f}" for d in drift))
    if max(np.abs(drift)) > 25:
        print("WARNING: the grid drifts - tempo change or a wrong period; beat rewards will be off in part of the song")

    # bar start: how much the harmony changes from the beat before to this one
    hop = 256
    chroma = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y), sr=SR, hop_length=hop)
    beats = np.arange(ph, len(y) / SR - p, p)
    fr = np.round(beats * SR / hop).astype(int)
    cb = np.stack([chroma[:, a:b].mean(1) for a, b in zip(fr, fr[1:])])
    cb /= np.linalg.norm(cb, axis=1, keepdims=True) + 1e-9
    nov = np.r_[0, 1 - (cb[1:] * cb[:-1]).sum(1)]
    by4 = [nov[i::4].mean() for i in range(4)]
    r = int(np.argmax(by4))
    by8 = [nov[i::8].mean() for i in (r, r + 4)]
    r8 = r + 4 * int(np.argmax(by8))
    print("harmony change by beat of the bar: " + " ".join(f"{v:.3f}" for v in by4) + f"  -> bars start on beat {r}")
    print(f"two-bar phrases: {by8[0]:.3f} vs {by8[1]:.3f}  -> phase moved by {r8} beats")
    phase = ph + r8 * p
    with open(sys.argv[2], "w") as f:
        json.dump({"period": float(p), "phase": float(phase)}, f)
    print(f"wrote {sys.argv[2]}: period {p:.6f}, phase {phase:.4f}")


if __name__ == "__main__":
    main()
