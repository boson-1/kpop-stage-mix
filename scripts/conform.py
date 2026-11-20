"""Conform every stage video onto the common song timeline.

Output: one intra-friendly 1080p / 29.97 fps file per stage where frame f shows
song time  f / FPS - PRE_ROLL.  After this step "same frame number" means "same
moment of the choreography" in every stage, so the later steps can address
frames by index only.

usage: conform.py SYNC.json SOURCES_DIR OUT_DIR [NAME ...]
"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FPS = "30000/1001"
PRE_ROLL = 30 * 1001 / 30000      # 30 frames of lead-in before the song starts
POST_ROLL = 1.0


def conform(name, clip, dur, src_dir, out_dir):
    out = out_dir / f"{name}.mp4"
    start = clip["offset"] + clip.get("vdelay", 0.0) - PRE_ROLL
    seek, vf = ["-ss", f"{start:.4f}"], f"fps={FPS}"
    if start < 0:        # the clip starts after the song did: hold its first frame until its picture begins
        seek, vf = [], f"setpts=PTS+{-start:.4f}/TB,fps={FPS}:start_time=0"
    cmd = ["ffmpeg", "-v", "error", "-y", *seek, "-i", str(src_dir / f"{name}.mp4"),
           "-t", f"{dur + PRE_ROLL + POST_ROLL:.4f}", "-an",
           "-vf", f"{vf},scale=1920:1080:flags=lanczos,setsar=1",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-g", "15", "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(cmd, check=True)
    n = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                        "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout.strip()
    print(f"{name}: start {start:.3f}s -> {n} frames", flush=True)


def main():
    sync = json.loads(Path(sys.argv[1]).read_text())
    src_dir, out_dir = Path(sys.argv[2]), Path(sys.argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)
    names = sys.argv[4:] or sorted(n for n in sync["clips"] if n[:2].isdigit())
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(lambda n: conform(n, sync["clips"][n], sync["ref_dur"], src_dir, out_dir), names))


if __name__ == "__main__":
    main()
