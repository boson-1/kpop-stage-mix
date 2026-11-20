"""Check a full-length render against its plan, and list what a person should look at.

1. the file decodes without errors
2. its sound is the reference track at the planned position (three places)
3. the virtual camera never shows a border
4. un-zoomed shots show exactly the planned frame of the planned stage, levels untouched
5. to watch: fast virtual-camera moves and the weakest face matches

Exits non-zero when one of 1-4 fails.

usage: verify.py WORK_DIR EDL.json OUT.mp4
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import correlate

sys.path.insert(0, str(Path(__file__).parent))
import render  # noqa: E402
import stagemix as sm  # noqa: E402

W, H = 128, 72


def gray(path, start=None, n=None):
    """Frames of a 29.97 fps video as 128x72 gray; start / n pick frames by index."""
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, (start - 0.5) / sm.FPS):.5f}"]
    cmd += ["-i", str(path)]
    if n is not None:
        cmd += ["-frames:v", str(n)]
    cmd += ["-vf", f"scale={W}:{H}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.uint8).reshape(-1, H, W)


def clock(frames):
    t = frames / sm.FPS
    return f"{int(t // 60)}:{t % 60:04.1f}"


def main():
    work, out = Path(sys.argv[1]), sys.argv[3]
    edl = json.loads(Path(sys.argv[2]).read_text())
    sh, f0 = edl["shots"], edl["f0"]
    failed = []

    # 1
    print(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,width,height,r_frame_rate,pix_fmt,color_space,nb_frames:format=duration",
                          "-of", "default=nw=1", out], capture_output=True, text=True).stdout.strip().replace("\n", "  "))
    err = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "null", "-"], capture_output=True, text=True).stderr.strip()
    print("decode:", err or "no errors")
    if err:
        failed.append("decode")

    # 2
    ref_name = json.loads((work / "sync.json").read_text())["ref"]
    ref, sr = sf.read(work / "audio" / f"{ref_name}.wav", dtype="float32")
    a = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                                     capture_output=True).stdout, np.float32)
    t0, dur = (f0 - edl["pre"]) / sm.FPS, (edl["f1"] - f0) / sm.FPS           # song time of the first output frame
    errs = []
    for t in (int(0.1 * dur), int(0.5 * dur), int(0.85 * dur)):
        lag = int(np.argmax(correlate(ref, a[t * sr:(t + 15) * sr], mode="valid", method="fft"))) / sr
        errs.append(1000 * (lag - t - t0))
    print(f"audio: against {ref_name} at three places " + " ".join(f"{e:+.1f}" for e in errs) + " ms")
    if max(abs(e) for e in errs) > 10:
        failed.append("audio offset")

    # 3
    shots = [render.Shot(s, sh[i + 1] if i + 1 < len(sh) else None, edl["cuts"][s["src"]], work / "conf", 0)
             for i, s in enumerate(sh)]
    border, zmax, n = 0, 1.0, 0
    for s in shots:
        for f in range(s.first, s.last):
            z, o = s.camera(f)
            n, zmax = n + 1, max(zmax, z)
            border += bool(z < 1 - 1e-6 or o.max() > 1e-6 or z * sm.W + o[0] < sm.W - 1e-6 or z * sm.H + o[1] < sm.H - 1e-6)
    print(f"camera: {n} shot frames (a dissolve counts for both shots), {border} showing a border, largest zoom {zmax:.2f}")
    if border:
        failed.append("border")

    # 4
    mix = gray(out)
    level, wrong = [], []
    for i, s in enumerate(shots):
        f = (s.start + s.end) // 2
        z, o = s.camera(f)
        if abs(z - 1) > 1e-4 or np.abs(o).max() > 0.05 or not 0 <= f - f0 < len(mix):
            continue
        d = np.abs(gray(s.path, f - 3, 7).astype(np.float32) - mix[f - f0]).mean((1, 2))
        level.append(d[3])
        if int(np.argmin(d)) != 3 and d.min() < 0.7 * d[3]:                   # a still picture matches its neighbours too
            wrong.append(f"#{i} ({int(np.argmin(d)) - 3:+d})")
    print(f"placement: {len(level)} un-zoomed shots compared with their stage, {len(wrong)} on a wrong frame "
          + " ".join(wrong) + f"; median difference {np.median(level):.2f} of 255")
    if wrong or np.median(level) > 3:
        failed.append("placement / levels")

    # 5
    print("to watch (time in the output file):")
    for i, s in enumerate(shots):
        if s.cuts:
            continue                                                          # the camera holds, then resets on the cut
        (zi, oi), (zo, oo) = s.cam_in, s.cam_out
        sec = (s.end - s.start) / sm.FPS
        centre = np.array([sm.W / 2, sm.H / 2])
        zoom, pan = abs(np.log(zo / zi)) / sec, np.linalg.norm((centre - oo) / zo - (centre - oi) / zi) / sec
        if zoom > 0.12 or pan > 110:
            print(f"  shot #{i} at {clock(s.start - f0)}: zoom {zi:.2f} -> {zo:.2f} and {pan * sec:.0f} px pan in {sec:.1f} s")
    weak = sorted((s["q"], i) for i, s in enumerate(sh) if s["kind"] == "match")[:5]
    print("  weakest face matches: " + ", ".join(f"#{i} at {clock(sh[i]['start'] - f0)} (q {q:.2f})" for q, i in weak))

    if failed:
        sys.exit("FAILED: " + ", ".join(failed))
    print("all checks passed")


if __name__ == "__main__":
    main()
