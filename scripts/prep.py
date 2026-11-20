"""Extract what the analysis steps read from every downloaded file.

Per FILE:  WORK_DIR/audio/NAME.wav   22.05 kHz mono (sync.py, beats.py)
and, when it has a picture:
           WORK_DIR/lowres/NAME.raw  128x72 gray at the native frame rate (vdelay.py, analyze_mix.py)
           its frame rate in WORK_DIR/lowres/fps.json

usage: prep.py WORK_DIR FILE [FILE ...]
"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path


def probe(path, stream, entry):
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", f"stream={entry}",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip().split("\n")[0]


def prep(path, work):
    name = path.stem
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "22050",
                    str(work / "audio" / f"{name}.wav")], check=True)
    rate = probe(path, "v:0", "r_frame_rate")
    if not rate:
        print(f"{name}: audio only", flush=True)
        return name, None
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-an", "-vf", "scale=128:72:flags=area",
                    "-f", "rawvideo", "-pix_fmt", "gray", str(work / "lowres" / f"{name}.raw")], check=True)
    fps = float(Fraction(rate))
    # every later step assumes picture and sound both start at 0 inside the file
    gap = float(probe(path, "v:0", "start_time") or 0) - float(probe(path, "a:0", "start_time") or 0)
    warn = f"  WARNING: picture starts {gap * 1000:+.0f} ms against the sound inside the file" if abs(gap) > 0.005 else ""
    print(f"{name}: {probe(path, 'v:0', 'width')}x{probe(path, 'v:0', 'height')} {fps:.3f} fps{warn}", flush=True)
    return name, fps


def main():
    work, files = Path(sys.argv[1]), [Path(f) for f in sys.argv[2:]]
    (work / "audio").mkdir(parents=True, exist_ok=True)
    (work / "lowres").mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda f: prep(f, work), files))
    fps_path = work / "lowres" / "fps.json"
    fps = json.loads(fps_path.read_text()) if fps_path.exists() else {}
    fps.update({n: v for n, v in res if v})
    fps_path.write_text(json.dumps(fps, indent=1))


if __name__ == "__main__":
    main()
