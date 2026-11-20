"""Side-by-side video of your mix and up to three other mixes of the same song.

Every cell (1920x1080, 59.94 fps) shows the same moment of the song: each other mix is lined
up through its own audio offset in SYNC.json, so it plays exactly as its author timed it to
the music.  Run prep.py and sync.py on the other mixes first; a mix is looked up in SYNC.json
by its file name without extension.  Sound: your mix.  Two files give 3840x1080, more 3840x2160.

usage: compare.py SYNC.json EDL.json OUT.mp4 MINE.mp4 OTHER.mp4 [OTHER.mp4 [OTHER.mp4]]
"""
import json
import subprocess
import sys
from pathlib import Path


def main():
    clips = json.loads(Path(sys.argv[1]).read_text())["clips"]
    edl = json.loads(Path(sys.argv[2]).read_text())
    out, mine, others = sys.argv[3], sys.argv[4], sys.argv[5:8]
    t0 = (edl["f0"] - edl["pre"]) / edl["fps"]                 # song time at which MINE starts
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", mine],
                         capture_output=True, text=True).stdout.strip()
    cmd = ["ffmpeg", "-v", "error", "-stats", "-y", "-i", mine]
    for o in others:
        cmd += ["-ss", f"{clips[Path(o).stem]['offset'] + t0:.4f}", "-i", o]
    labels = ["THIS EDIT"] + [Path(o).stem for o in others]
    # other mixes stop before the studio track does: go to black instead of freezing
    cells = [f"[{i}:v]fps=60000/1001,scale=1920:1080,setsar=1,tpad=stop_mode=add:stop_duration=8:color=black,"
             f"drawtext=text='{lab}':fontsize=40:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=12:"
             f"x=(w-text_w)/2:y=18[c{i}]" for i, lab in enumerate(labels)]
    layout = "|".join(["0_0", "w0_0", "0_h0", "w0_h0"][:len(labels)])
    graph = ";".join(cells) + ";" + "".join(f"[c{i}]" for i in range(len(labels))) \
        + f"xstack=inputs={len(labels)}:layout={layout}:fill=black[v]"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "0:a", "-t", dur, "-r", "60000/1001",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "copy", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
