"""Detect every face in every frame of one conformed stage video.

Per face we keep the box, the five landmarks (eyes, nose, mouth corners) and,
for faces big enough to matter in a close-up, an identity embedding so a later
step can insist that both sides of an outfit change show the same member.

usage: faces.py CONF_DIR OUT_DIR NAME [THREADS]
Output: OUT_DIR/NAME.npz with one row per detected face (coordinates in 1920x1080).
"""
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from insightface.app import FaceAnalysis
from insightface.utils import face_align

W, H = 1920, 1080
DET_SCALE = 0.5            # detection runs on a 960x540 copy
MIN_EMB_H = 64             # px (1080p) - smaller faces get no identity embedding


def main():
    conf_dir, out_dir, name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    threads = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    out_dir.mkdir(parents=True, exist_ok=True)

    kwargs = {}
    if "CUDAExecutionProvider" in ort.get_available_providers():
        kwargs["providers"] = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    else:
        kwargs["providers"] = ["CPUExecutionProvider"]
        if threads:
            so = ort.SessionOptions()
            so.intra_op_num_threads = threads
            kwargs["sess_options"] = so
    app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "recognition"], **kwargs)
    app.prepare(ctx_id=0, det_size=(960, 960), det_thresh=0.45)
    det, rec = app.models["detection"], app.models["recognition"]
    print(f"{name}: face models run on {det.session.get_providers()[0]}", flush=True)

    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(conf_dir / f"{name}.mp4"),
                             "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    rows_f, rows_box, rows_score, rows_kps, rows_emb = [], [], [], [], []
    f = 0
    while True:
        buf = proc.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        small = cv2.resize(img, None, fx=DET_SCALE, fy=DET_SCALE, interpolation=cv2.INTER_AREA)
        boxes, kpss = det.detect(small, max_num=0, metric="default")
        if len(boxes):
            boxes[:, :4] /= DET_SCALE
            kpss = kpss / DET_SCALE
            big = [i for i in range(len(boxes)) if boxes[i, 3] - boxes[i, 1] >= MIN_EMB_H]
            embs = np.full((len(boxes), 512), np.nan, np.float32)
            if big:
                crops = [face_align.norm_crop(img, landmark=kpss[i], image_size=112) for i in big]
                feats = rec.get_feat(crops)
                embs[big] = feats / np.linalg.norm(feats, axis=1, keepdims=True)
            rows_f += [f] * len(boxes)
            rows_box.append(boxes[:, :4])
            rows_score.append(boxes[:, 4])
            rows_kps.append(kpss)
            rows_emb.append(embs)
        f += 1
        if f % 1000 == 0:
            print(f"{name}: {f} frames", flush=True)
    proc.wait()
    np.savez_compressed(out_dir / f"{name}.npz", n_frames=f,
                        frame=np.array(rows_f, np.int32),
                        box=np.concatenate(rows_box).astype(np.float32),
                        score=np.concatenate(rows_score).astype(np.float32),
                        kps=np.concatenate(rows_kps).astype(np.float32),
                        emb=np.concatenate(rows_emb).astype(np.float16))
    print(f"{name}: done, {f} frames, {len(rows_f)} faces", flush=True)


if __name__ == "__main__":
    main()
